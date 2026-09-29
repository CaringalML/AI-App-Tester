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
  let next = window.__aatRef || 0;
  const elements = [];
  for (const el of document.querySelectorAll(selector)) {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (box.width === 0 || box.height === 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    let ref = el.getAttribute('data-aat-ref');
    if (!ref) { ref = 'e' + (++next); el.setAttribute('data-aat-ref', ref); }
    const tag = el.tagName.toLowerCase();
    const labelled = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
    const label = (el.getAttribute('aria-label') || (labelled && labelled.innerText) || el.innerText ||
      el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') ||
      el.getAttribute('name') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    const item = { ref, tag, label };
    const type = el.getAttribute('type') || el.getAttribute('role');
    if (type) item.type = type;
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
        self._pw = await async_playwright().start()
        # Fargate's /dev/shm is tiny; without this Chromium crashes on heavy pages.
        self._browser = await self._pw.chromium.launch(args=["--disable-dev-shm-usage"])
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

    # ---- evidence capture -------------------------------------------------

    async def _guard_route(self, route: Route) -> None:
        url = route.request.url
        scheme = urlsplit(url).scheme
        if scheme in ("data", "blob", "about", "chrome-extension"):
            await route.continue_()
            return
        if await self.guard.is_allowed_host(urlsplit(url).hostname):
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

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
        if "ERR_BLOCKED_BY_CLIENT" in failure:
            self.log.add("blocked-request", self._current(), f"Blocked request to {request.url}")
            return
        if "ERR_ABORTED" in failure:
            return  # navigations cancelled by the next navigation, not a defect
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
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
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
            if not await self.guard.is_allowed_host(urlsplit(link).hostname):
                continue
            try:
                response = await self._context.request.get(link, timeout=8_000, max_redirects=5)
                status = response.status
                await response.dispose()
            except PlaywrightError as exc:
                self.log.add(
                    "broken-link", found_on, f"{link} could not be fetched: {exc}", url=link
                )
                continue
            if status >= 400:
                self.log.add(
                    "broken-link",
                    found_on,
                    f"Link to {link} returns HTTP {status}",
                    url=link,
                    status=status,
                )

    async def screenshot(self) -> bytes:
        assert self.page is not None
        return await self.page.screenshot(type="jpeg", quality=60)

    async def snapshot(self) -> dict:
        assert self.page is not None
        return await self.page.evaluate(_SNAPSHOT_JS)

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
