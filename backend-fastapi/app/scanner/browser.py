"""A Playwright session that records evidence as it goes.

Listeners capture console errors, uncaught exceptions, failed and erroring
network requests, and dialogs into the ObservationLog. Each page the session
reaches is audited once: load timing, basic document metadata, and axe-core
accessibility rules. The agent drives the page through `act_*` methods that
report what changed, so its findings can cite concrete action ids.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Dialog,
    Page,
    Playwright,
    Request,
    Response,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..security import TargetGuard
from .egress import EgressProxy
from .observations import ObservationLog

log = logging.getLogger(__name__)

VIEWPORT = {"width": 1280, "height": 800}
NAV_TIMEOUT_MS = 20_000
ACTION_TIMEOUT_MS = 6_000

_SNAPSHOT_JS = """
() => {
  const selector = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[role=button]', '[role=link]', '[role=tab]', '[role=checkbox]', '[role=menuitem]',
    '[role=switch]', '[contenteditable=true]'
  ].join(',');
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  // The name a screen reader announces, in the order browsers work it out:
  // aria-labelledby, aria-label, a <label>, a submit button's value, then the
  // element's own content, which includes the alt text of images inside it
  // (an image-only button is named by its image), then title and placeholder.
  // Reading innerText alone made every image button look nameless.
  const nameOf = (el, tag) => {
    const byIds = (el.getAttribute('aria-labelledby') || '').split(/\\s+/)
      .map((id) => id && document.getElementById(id)).filter(Boolean)
      .map((n) => clean(n.innerText || n.textContent)).join(' ');
    if (byIds) return byIds;
    const aria = clean(el.getAttribute('aria-label'));
    if (aria) return aria;
    if (el.labels && el.labels.length) {
      const labels = clean([...el.labels].map((l) => l.innerText).join(' '));
      if (labels) return labels;
    }
    if (tag === 'input' && ['submit', 'button', 'reset'].includes(el.type) && clean(el.value)) {
      return clean(el.value);
    }
    if (tag === 'input' && el.type === 'image' && clean(el.getAttribute('alt'))) {
      return clean(el.getAttribute('alt'));
    }
    if (!['input', 'select', 'textarea'].includes(tag)) {
      const own = clean(el.innerText);
      if (own) return own;
      const inner = [...el.querySelectorAll('img[alt], [role=img][aria-label], svg[aria-label], svg title')]
        .map((n) => clean(n.getAttribute('alt') || n.getAttribute('aria-label') || n.textContent))
        .filter(Boolean).join(' ');
      if (inner) return inner;
    }
    return clean(el.getAttribute('title') || el.getAttribute('placeholder'));
  };
  let next = window.__aatRef || 0;
  const elements = [];
  for (const el of document.querySelectorAll(selector)) {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (box.width === 0 || box.height === 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    let ref = el.getAttribute('data-aat-ref');
    if (!ref) { ref = 'e' + (++next); el.setAttribute('data-aat-ref', ref); }
    const tag = el.tagName.toLowerCase();
    const item = { ref, tag, label: nameOf(el, tag).slice(0, 80) };
    const type = el.getAttribute('type') || el.getAttribute('role');
    if (type) item.type = type;
    // A form field's name attribute identifies it without being mistaken for a label.
    if (['input', 'select', 'textarea'].includes(tag) && el.getAttribute('name')) {
      item.field = el.getAttribute('name').slice(0, 40);
    }
    if (tag === 'a') item.href = el.getAttribute('href');
    if (el.disabled) item.disabled = true;
    if (el.required) item.required = true;
    if ((tag === 'input' || tag === 'textarea') && el.type !== 'password' && el.value) item.value = el.value.slice(0, 40);
    if (tag === 'select') item.options = [...el.options].slice(0, 12).map(o => o.label);
    elements.push(item);
    if (elements.length >= 90) break;
  }
  window.__aatRef = next;
  const text = (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n').slice(0, 3500);
  const alerts = [...document.querySelectorAll('[role=alert], [aria-live=assertive], [aria-invalid=true]')]
    .map(e => (e.innerText || e.getAttribute('aria-label') || e.getAttribute('name') || '').trim())
    .filter(Boolean).slice(0, 6);
  return { url: location.href, title: document.title, elements, text, alerts };
}
"""

_PAGE_FACTS_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const meta = name => { const m = document.querySelector(`meta[name="${name}"]`); return m ? m.getAttribute('content') : null; };
  const links = [...new Set([...document.querySelectorAll('a[href]')].map(a => a.href))]
    .filter(h => h.startsWith('http')).slice(0, 80);
  return {
    title: document.title,
    description: meta('description'),
    viewport: meta('viewport'),
    lang: document.documentElement.getAttribute('lang'),
    h1Count: document.querySelectorAll('h1').length,
    timing: nav ? {
      domContentLoaded: Math.round(nav.domContentLoadedEventEnd),
      load: Math.round(nav.loadEventEnd),
      transferSize: nav.transferSize || 0,
    } : null,
    links,
  };
}
"""

_AXE_JS = """
async () => {
  if (!window.axe) return null;
  const result = await window.axe.run(document, {
    resultTypes: ['violations'],
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  });
  return result.violations.map(v => ({
    id: v.id, impact: v.impact, help: v.help, helpUrl: v.helpUrl,
    count: v.nodes.length,
    nodes: v.nodes.slice(0, 4).map(n => ({ target: n.target.join(' '), html: n.html.slice(0, 180) })),
  }));
}
"""


@dataclass
class ActionOutcome:
    """What an agent action did: its ledger id, a description, and where it ended up."""

    id: str
    text: str
    failed: bool
    url: str
    signals: int = 0

    def for_model(self) -> str:
        return f"{self.id}: {self.text}"


# Locator candidates for an element, best first, in the order Playwright's docs
# recommend. Checked for uniqueness in Python before one is used.
_LOCATOR_CANDIDATES_JS = """
(el) => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  let role = el.getAttribute('role');
  if (!role) {
    if (tag === 'button' || (tag === 'input' && ['submit', 'button', 'reset'].includes(type))) role = 'button';
    else if (tag === 'a' && el.hasAttribute('href')) role = 'link';
    else if (tag === 'input' && type === 'checkbox') role = 'checkbox';
    else if (tag === 'input' && type === 'radio') role = 'radio';
    else if (tag === 'select') role = 'combobox';
    else if (tag === 'textarea' || (tag === 'input' && ['', 'text', 'email', 'search', 'tel', 'url', 'number'].includes(type))) role = 'textbox';
  }
  const labelEl = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : el.closest('label');
  const label = clean(el.getAttribute('aria-label') || (labelEl && labelEl.innerText));
  const ownText = clean(el.innerText || el.value || el.getAttribute('title') || (el.querySelector('img[alt]') || {}).alt);
  const name = clean(el.getAttribute('aria-label')) || (['button', 'link', 'tab', 'menuitem', 'checkbox', 'radio'].includes(role) ? ownText : '') || label;
  const out = [];
  const testid = el.getAttribute('data-testid');
  if (testid) out.push({ kind: 'testid', value: testid });
  for (const attr of ['data-test', 'data-cy']) {
    const v = el.getAttribute(attr);
    if (v) out.push({ kind: 'css', value: `[${attr}="${v.replace(/"/g, '\\\\"')}"]` });
  }
  if (role && name && name.length <= 80) out.push({ kind: 'role', role, name });
  if (label && label.length <= 80) out.push({ kind: 'label', value: label });
  const placeholder = el.getAttribute('placeholder');
  if (placeholder) out.push({ kind: 'placeholder', value: placeholder });
  if (el.id && /^[A-Za-z][\\w-]*$/.test(el.id)) out.push({ kind: 'css', value: '#' + el.id });
  const nameAttr = el.getAttribute('name');
  if (nameAttr && /^[\\w-]+$/.test(nameAttr)) out.push({ kind: 'css', value: `${tag}[name="${nameAttr}"]` });
  if (ownText && ownText.length <= 60) out.push({ kind: 'text', value: ownText });
  return out;
}
"""


def site_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def page_key(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path.rstrip("/") or "/").lower()


class BrowserSession:
    def __init__(
        self,
        guard: TargetGuard,
        log_: ObservationLog,
        *,
        accessibility: bool,
        axe_path: str,
    ) -> None:
        self.guard = guard
        # Every connection Chromium makes goes through this; see egress.py for why.
        self.egress = EgressProxy(guard.allow_private)
        self.log = log_
        self.accessibility = accessibility
        self.axe_source = _load_axe(axe_path) if accessibility else None
        self.site: str | None = None
        self.visited: list[str] = []
        self.links: dict[str, str] = {}  # same-site link -> page it was found on
        self._audited: set[str] = set()
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    @property
    def accessibility_available(self) -> bool:
        return self.axe_source is not None

    async def __aenter__(self) -> "BrowserSession":
        proxy_url = await self.egress.start()
        self._pw = await async_playwright().start()
        # Fargate's /dev/shm is tiny; without this Chromium crashes on heavy pages.
        # Playwright also routes loopback through a configured proxy, and WebRTC is
        # kept off direct UDP, so no connection goes around the egress proxy.
        self._browser = await self._pw.chromium.launch(
            args=[
                "--disable-dev-shm-usage",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
            proxy={"server": proxy_url},
        )
        self._context = await self._browser.new_context(
            viewport=VIEWPORT,
            user_agent=None,
            locale="en-NZ",
        )
        if self.axe_source:
            # Injected through the DevTools protocol, so a strict Content-Security-Policy
            # on the target cannot block it the way it would block a <script> tag.
            await self._context.add_init_script(script=self.axe_source)
        await self._context.route("**/*", self._guard_route)
        self.page = await self._context.new_page()
        self._attach_listeners(self.page)
        # Registered after the main page exists: the "page" event also fires for
        # new_page() itself, and would otherwise close the scanner's own tab.
        self._context.on("page", self._on_popup)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    await closer.close()
                except PlaywrightError:
                    pass
        if self._pw is not None:
            await self._pw.stop()
        await self.egress.close()

    # ---- evidence capture -------------------------------------------------

    async def _guard_route(self, route: Route) -> None:
        url = route.request.url
        scheme = urlsplit(url).scheme
        if scheme in ("data", "blob", "about", "chrome-extension"):
            await route.continue_()
            return
        host = urlsplit(url).hostname
        if not host:
            await route.abort("blockedbyclient")
            return
        # The egress proxy decides for every connection; asking it here fails a
        # refused request before Chromium even connects. A name that does not
        # resolve is not refused: it fails on its own and is reported as
        # unreachable, which for a third-party script is a real finding.
        await self.egress.addresses(host)
        if self.egress.is_refused(host):
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    def _attach_listeners(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("requestfailed", self._on_request_failed)
        page.on("response", self._on_response)
        page.on("dialog", self._on_dialog)

    def _current(self) -> str:
        return self.page.url if self.page else ""

    def _on_console(self, message) -> None:  # noqa: ANN001 - playwright ConsoleMessage
        if message.type != "error":
            return
        text = message.text
        # The network listener already records these with the status code attached.
        if text.startswith("Failed to load resource"):
            return
        location = message.location or {}
        self.log.add(
            "console-error",
            self._current(),
            text[:600],
            source=location.get("url"),
            line=location.get("lineNumber"),
        )

    def _on_page_error(self, error: PlaywrightError) -> None:
        stack = (getattr(error, "stack", None) or "").splitlines()[:6]
        self.log.add("page-error", self._current(), str(error)[:600], stack="\n".join(stack))

    def _on_request_failed(self, request: Request) -> None:
        failure = request.failure or "unknown failure"
        host = urlsplit(request.url).hostname
        # Refused by the route guard or the egress proxy: the scanner's own safety
        # rule at work, not a defect in the site.
        if "ERR_BLOCKED_BY_CLIENT" in failure or self.egress.is_refused(host):
            self.log.add("blocked-request", self._current(), f"Blocked request to {request.url}")
            return
        if "ERR_ABORTED" in failure:
            return  # navigations cancelled by the next navigation, not a defect
        # A host the proxy could not reach fails in Chromium as a proxy error; report
        # the real reason, the way Chromium would without a proxy.
        proxy_failure = self.egress.failures.get((host or "").lower())
        if proxy_failure and ("TUNNEL" in failure or "EMPTY_RESPONSE" in failure):
            failure = proxy_failure
        self.log.add(
            "request-failed",
            self._current(),
            f"{request.method} {request.url} failed: {failure}",
            url=request.url,
            resource=request.resource_type,
        )

    def _on_response(self, response: Response) -> None:
        if response.status < 400:
            return
        request = response.request
        self.log.add(
            "http-error",
            self._current(),
            f"{request.method} {response.url} returned HTTP {response.status}",
            url=response.url,
            status=response.status,
            resource=request.resource_type,
        )

    async def _on_dialog(self, dialog: Dialog) -> None:
        self.log.add("dialog", self._current(), f"{dialog.type} dialog: {dialog.message[:300]}")
        try:
            await dialog.dismiss() if dialog.type == "beforeunload" else await dialog.accept()
        except PlaywrightError:
            pass

    async def _on_popup(self, popup: Page) -> None:
        if popup is self.page:
            return
        self.log.add(
            "popup", self._current(), f"A new tab was opened to {popup.url or 'about:blank'}"
        )
        try:
            await popup.close()
        except PlaywrightError:
            pass

    # ---- navigation and auditing -------------------------------------------

    def same_site(self, url: str) -> bool:
        return self.site is not None and site_key(url) == self.site

    async def open(self, url: str) -> int | None:
        assert self.page is not None
        refused_before = len(self.egress.refused)
        try:
            response = await self.page.goto(
                url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
            )
        except PlaywrightError as exc:
            # goto rejects as soon as the request fails, but Chromium commits its
            # error page a moment later; wait for it, or that late commit cancels
            # whatever navigation comes next and makes a good page look broken.
            try:
                await self.page.wait_for_url("chrome-error://**", timeout=1_000)
            except PlaywrightError:
                pass
            refused = self.egress.refused[refused_before:]
            if refused:
                raise PlaywrightError(
                    f"it redirected to {refused[0]}, a private or reserved address, "
                    "which the tester does not open"
                ) from exc
            raise
        if self.site is None:
            self.site = site_key(self.page.url)
        await self.settle()
        await self.audit_current_page()
        return response.status if response else None

    async def settle(self, idle_ms: int = 4_000) -> None:
        assert self.page is not None
        try:
            await self.page.wait_for_load_state("networkidle", timeout=idle_ms)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(0.3)

    async def audit_current_page(self) -> bool:
        """Audit the page once per path. Returns True if this was a new page."""
        assert self.page is not None
        url = self.page.url
        key = page_key(url)
        if key in self._audited or not self.same_site(url):
            return False
        self._audited.add(key)
        self.visited.append(url)

        try:
            facts = await self.page.evaluate(_PAGE_FACTS_JS)
        except PlaywrightError:
            facts = None
        if facts:
            self.log.add("page-facts", url, f"Audited {url}", **facts)
            for link in facts.get("links", []):
                if self.same_site(link):
                    self.links.setdefault(link.split("#")[0], url)

        if self.axe_source:
            try:
                violations = await self.page.evaluate(_AXE_JS)
            except PlaywrightError as exc:
                log.warning("axe failed on %s: %s", url, exc)
                violations = None
            for violation in violations or []:
                self.log.add(
                    "a11y",
                    url,
                    f"{violation['help']} ({violation['count']} element(s))",
                    **violation,
                )
        return True

    async def check_links(self, limit: int) -> None:
        """Request same-site links that were not visited, to find ones that are broken."""
        assert self._context is not None
        visited_keys = {page_key(u) for u in self.visited}
        candidates = [
            (link, found_on)
            for link, found_on in self.links.items()
            if page_key(link) not in visited_keys
        ][:limit]
        for link, found_on in candidates:
            try:
                status = await self._link_status(link)
            except PlaywrightError as exc:
                self.log.add(
                    "broken-link", found_on, f"{link} could not be fetched: {exc}", url=link
                )
                continue
            if status is None:
                continue  # it leads somewhere the scanner does not go
            if status >= 400:
                self.log.add(
                    "broken-link",
                    found_on,
                    f"Link to {link} returns HTTP {status}",
                    url=link,
                    status=status,
                )

    async def _link_status(self, link: str, max_hops: int = 5) -> int | None:
        """Final HTTP status of a link, following redirects one hop at a time.

        The request API follows redirects outside the browser, where neither the
        route guard nor the egress proxy would see the hops, so each hop's host is
        checked here. None when a hop points somewhere the scanner does not go.
        """
        assert self._context is not None
        url = link
        for _ in range(max_hops + 1):
            if not await self.guard.is_allowed_host(urlsplit(url).hostname):
                return None
            response = await self._context.request.get(url, timeout=8_000, max_redirects=0)
            status, location = response.status, response.headers.get("location")
            await response.dispose()
            if not (300 <= status < 400 and location):
                return status
            url = urljoin(url, location)
        return status

    async def screenshot(self) -> bytes:
        assert self.page is not None
        return await self.page.screenshot(type="jpeg", quality=60)

    async def snapshot(self) -> dict:
        assert self.page is not None
        return await self.page.evaluate(_SNAPSHOT_JS)

    # ---- locators for exported tests -------------------------------------------

    async def stable_locator(self, ref: str) -> dict | None:
        """The locator a developer would write for this element, proven unique.

        Candidates are ordered the way Playwright's own guidance ranks them (test
        id, role and accessible name, label, placeholder, then CSS and text). Each
        one is checked with Playwright on the live page, and the first that
        matches exactly one element wins. Our internal data-aat-ref is never used,
        since it only exists while the scanner is attached.
        """
        try:
            handle = await self._locator(ref).element_handle(timeout=2_000)
            if handle is None:
                return None
            candidates = await handle.evaluate(_LOCATOR_CANDIDATES_JS)
        except (PlaywrightError, ValueError):
            return None
        for candidate in candidates or []:
            variants = [candidate]
            if candidate.get("kind") == "role":
                # Icon fonts put private-use glyphs into the accessible name (" Login"
                # becomes " Login"), which defeats an exact match. Playwright's
                # non-exact match accepts a substring, and is still proven unique below.
                variants.append({**candidate, "exact": False})
            for variant in variants:
                locator = self.locator_for(variant)
                if locator is None:
                    continue
                try:
                    if await locator.count() == 1:
                        return variant
                except PlaywrightError:
                    continue
        return None

    def locator_for(self, desc: dict, page: Page | None = None):  # noqa: ANN201 - Locator
        """The Python twin of playwright_export.render_locator, for checking."""
        page = page or self.page
        assert page is not None
        match desc.get("kind"):
            case "role":
                return page.get_by_role(
                    desc["role"], name=desc["name"], exact=desc.get("exact", True)
                )
            case "label":
                return page.get_by_label(desc["value"], exact=True)
            case "placeholder":
                return page.get_by_placeholder(desc["value"], exact=True)
            case "testid":
                return page.get_by_test_id(desc["value"])
            case "text":
                return page.get_by_text(desc["value"], exact=True)
            case "css":
                return page.locator(desc["value"])
        return None

    async def expectation_holds(
        self, kind: str, text: str, desc: dict | None, page: Page | None = None
    ) -> bool | None:
        """Whether an expectation is already true on the page right now.

        None when it cannot be checked. For a regression test, "already true"
        means the test would pass today and so may not catch the issue.
        """
        page = page or self.page
        assert page is not None
        text = text.strip()
        try:
            match kind:
                case "text_visible" if text:
                    return await page.get_by_text(text).first.is_visible()
                case "text_not_visible" if text:
                    return await page.get_by_text(text).count() == 0
                case "url_contains" if text:
                    return text in page.url
                case "url_not_contains" if text:
                    return text not in page.url
                case "element_visible" if desc:
                    return await self.locator_for(desc, page).is_visible()
                case "element_hidden" if desc:
                    return not await self.locator_for(desc, page).is_visible()
                case "element_text" if desc and text:
                    return text in (await self.locator_for(desc, page).inner_text(timeout=2_000))
        except PlaywrightError:
            return None
        return None

    async def replay_holds(
        self, start_url: str, actions: list, kind: str, text: str, desc: dict | None
    ) -> tuple[bool | None, str]:
        """Run a test's recorded steps in a fresh browser context, then check its assertion.

        This is what the Playwright runner will do with the exported file, done
        server-side before export: same steps, same locators, clean state. What
        runs is the recorded actions through this code, never model-written code.
        Returns (holds, reason); holds is None when the replay could not finish.
        """
        assert self._browser is not None
        context = await self._browser.new_context(viewport=VIEWPORT, locale="en-NZ")
        errors: list[str] = []
        try:
            # The SSRF guard applies to replays exactly as it does to the scan.
            await context.route("**/*", self._guard_route)
            page = await context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))
            page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
            await page.goto(start_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            for action in actions:
                locator = self.locator_for(action.locator, page) if action.locator else None
                match action.kind:
                    case "goto":
                        await page.goto(
                            action.value, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
                        )
                    case "back":
                        await page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    case "press":
                        await page.keyboard.press(action.value)
                    case "click" if locator is not None:
                        await locator.click(timeout=ACTION_TIMEOUT_MS)
                    case "fill" if locator is not None:
                        await locator.fill(action.value, timeout=ACTION_TIMEOUT_MS)
                    case "select" if locator is not None:
                        await locator.select_option(action.value, timeout=ACTION_TIMEOUT_MS)
                    case _:
                        return None, f"step {action.act_id} has no unique locator"
                try:
                    await page.wait_for_load_state("networkidle", timeout=3_000)
                except PlaywrightTimeout:
                    pass
            if kind == "no_console_errors":
                return not errors, ""
            return await self.expectation_holds(kind, text, desc, page), ""
        except PlaywrightError as exc:
            return None, str(exc).splitlines()[0][:160]
        finally:
            await context.close()

    # ---- agent actions ---------------------------------------------------------

    def _locator(self, ref: str):  # noqa: ANN202
        assert self.page is not None
        if not ref.startswith("e") or not ref[1:].isdigit():
            raise ValueError(
                f"'{ref}' is not an element ref. Use refs like e12 from get_page_state."
            )
        return self.page.locator(f'[data-aat-ref="{ref}"]').first

    async def target_box(self, ref: str) -> dict[str, float] | None:
        """Scroll the element into view and return its box as fractions of the viewport.

        Fractions, not pixels, so the UI can outline it on a screenshot at any size.
        """
        locator = self._locator(ref)
        try:
            await locator.scroll_into_view_if_needed(timeout=2_000)
            box = await locator.bounding_box(timeout=2_000)
        except PlaywrightError:
            return None
        if not box:
            return None
        width, height = VIEWPORT["width"], VIEWPORT["height"]
        return {
            "x": max(0.0, box["x"] / width),
            "y": max(0.0, box["y"] / height),
            "w": min(1.0, box["width"] / width),
            "h": min(1.0, box["height"] / height),
        }

    async def perform(self, description: str, action) -> ActionOutcome:  # noqa: ANN001
        """Run an action, then describe what changed."""
        assert self.page is not None
        before_url = self.page.url
        mark = len(self.log)
        error: str | None = None
        try:
            await action()
            await self.settle(idle_ms=3_000)
        except PlaywrightTimeout:
            error = "timed out (element may be hidden, covered, or disabled)"
        except PlaywrightError as exc:
            error = str(exc).splitlines()[0][:200]

        after_url = self.page.url
        new_page = await self.audit_current_page() if after_url != before_url else False
        signals = [o for o in self.log.since(mark) if o.kind not in ("page-facts",)]

        parts = [description]
        if error:
            parts.append(f"FAILED: {error}")
        if after_url != before_url:
            parts.append(
                f"URL changed to {after_url}" + (" (new page, audited)" if new_page else "")
            )
        else:
            parts.append("URL unchanged")
        if signals:
            parts.append(
                f"{len(signals)} new signal(s): " + "; ".join(s.brief(160) for s in signals)
            )
        else:
            parts.append("no errors observed")
        outcome = " | ".join(parts)
        action_obs = self.log.add("action", after_url, outcome, failed=bool(error))
        return ActionOutcome(action_obs.id, outcome, bool(error), after_url, len(signals))

    async def click(self, ref: str, label: str) -> ActionOutcome:
        return await self.perform(
            f'clicked {ref} "{label}"', lambda: self._locator(ref).click(timeout=ACTION_TIMEOUT_MS)
        )

    async def fill(self, ref: str, label: str, text: str) -> ActionOutcome:
        return await self.perform(
            f'typed "{text[:60]}" into {ref} "{label}"',
            lambda: self._locator(ref).fill(text, timeout=ACTION_TIMEOUT_MS),
        )

    async def select(self, ref: str, label: str, option: str) -> ActionOutcome:
        return await self.perform(
            f'selected "{option}" in {ref} "{label}"',
            lambda: self._locator(ref).select_option(option, timeout=ACTION_TIMEOUT_MS),
        )

    async def press(self, key: str) -> ActionOutcome:
        assert self.page is not None
        return await self.perform(f"pressed {key}", lambda: self.page.keyboard.press(key))

    async def navigate(self, target: str) -> ActionOutcome:
        assert self.page is not None
        url = urljoin(self.page.url, target)
        if not self.same_site(url):
            raise ValueError(f"{url} is outside the site under test; stay on {self.site}.")
        return await self.perform(
            f"navigated to {url}",
            lambda: self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS),
        )

    async def back(self) -> ActionOutcome:
        assert self.page is not None
        return await self.perform(
            "went back",
            lambda: self.page.go_back(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS),
        )


def _load_axe(path: str) -> str | None:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(__file__).resolve().parents[2] / path
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    log.warning("axe-core not found at %s; accessibility checks are disabled", candidate)
    return None
