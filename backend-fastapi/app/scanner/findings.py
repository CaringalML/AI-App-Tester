"""Turns raw browser evidence into findings.

Everything here is deterministic. These findings are produced from things the
browser verifiably recorded, so they carry high confidence by construction and
are the half of the report that cannot be a hallucination.
"""

import colorsys
import re
from collections import defaultdict
from urllib.parse import urlsplit

from ..models import Finding, ScanOptions
from .observations import Observation, ObservationLog

SLOW_LOAD_MS = 4_000
SLUGGISH_LOAD_MS = 2_500
HEAVY_PAGE_BYTES = 3_000_000


class FindingCollector:
    def __init__(self) -> None:
        self.items: list[Finding] = []

    def next_id(self) -> str:
        return f"f-{len(self.items) + 1}"

    def add(self, **fields: object) -> Finding:
        finding = Finding(id=self.next_id(), **fields)
        self.items.append(finding)
        return finding


def _path(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _short(url: str, limit: int = 70) -> str:
    """Path without the query string, for titles a person can read at a glance."""
    path = urlsplit(url).path or "/"
    return path if len(path) <= limit else path[: limit - 1] + "…"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _where(observations: list[Observation]) -> str:
    pages = sorted({_path(o.page) for o in observations if o.page})
    return pages[0] if len(pages) == 1 else f"{pages[0]} and {len(pages) - 1} other page(s)"


def _group(observations: list[Observation], key) -> dict[str, list[Observation]]:  # noqa: ANN001
    groups: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:
        groups[key(observation)].append(observation)
    return groups


def build_automated_findings(
    log: ObservationLog, collector: FindingCollector, options: ScanOptions
) -> None:
    if options.find_bugs:
        _crashes(log, collector)
        _console_errors(log, collector)
        _network(log, collector)
        _broken_links(log, collector)
    if options.check_accessibility:
        _accessibility(log, collector)
    if options.find_improvements:
        _performance(log, collector)
        _metadata(log, collector)


def _ids(observations: list[Observation]) -> list[str]:
    return [o.id for o in observations][:8]


def _crashes(log: ObservationLog, collector: FindingCollector) -> None:
    for message, group in _group(log.of_kind("page-error"), lambda o: o.text).items():
        first = group[0]
        stack = first.data.get("stack") or ""
        collector.add(
            title=f"Uncaught JavaScript error: {message[:90]}",
            category="bug",
            severity="high",
            confidence="high",
            kind="crash",
            source="automated",
            location=_where(group),
            evidence=f"Thrown {len(group)} time(s). {message}" + (f"\n{stack}" if stack else ""),
            steps=[f"Open {first.page}", "Open the browser developer console", "Reload the page"],
            suggestion=(
                "Fix the exception at the top of the stack. An uncaught error stops the rest "
                "of that script, so features further down it may silently not work."
            ),
            evidence_ids=_ids(group),
        )


def _console_errors(log: ObservationLog, collector: FindingCollector) -> None:
    for message, group in _group(log.of_kind("console-error"), lambda o: o.text[:160]).items():
        first = group[0]
        source = first.data.get("source")
        collector.add(
            title=f"Console error: {message[:90]}",
            category="bug",
            severity="medium",
            confidence="high",
            kind="console",
            source="automated",
            location=_where(group),
            evidence=f"Logged {len(group)} time(s): {first.text}"
            + (f"\nSource: {source}:{first.data.get('line')}" if source else ""),
            steps=[f"Open {first.page}", "Open the browser developer console"],
            suggestion="Trace the error to its source and fix or handle it; console errors usually "
            "mean a feature failed for the user without any visible message.",
            evidence_ids=_ids(group),
        )


def _network(log: ObservationLog, collector: FindingCollector) -> None:
    http_errors = log.of_kind("http-error")
    by_url = _group(http_errors, lambda o: f"{o.data.get('status')} {o.data.get('url')}")
    for _, group in by_url.items():
        first = group[0]
        status = int(first.data.get("status") or 0)
        resource = first.data.get("resource") or "resource"
        url = first.data.get("url") or ""
        is_favicon = url.endswith("favicon.ico")

        if status >= 500:
            severity, noun = "high", "Server error"
        elif resource in ("document", "script", "stylesheet", "fetch", "xhr"):
            severity, noun = "medium", "Request failed"
        else:
            severity, noun = "low", "Missing asset"
        # A 401/403 from an API can be deliberate (signed-out state), so it is flagged
        # but not asserted with full confidence.
        confidence = "medium" if status in (401, 403) and resource in ("fetch", "xhr") else "high"
        if is_favicon:
            severity, noun = "low", "Missing favicon"

        collector.add(
            title=f"{noun}: {resource} {_short(url)} returns HTTP {status}",
            category="bug",
            severity=severity,
            confidence=confidence,
            kind="network",
            source="automated",
            location=_where(group),
            evidence=f"{first.text} (seen {len(group)} time(s))",
            steps=[f"Open {first.page}", "Open developer tools, Network tab", f"Filter for {url}"],
            suggestion="Fix the endpoint or the reference to it."
            if status < 500
            else "Check the server logs for this route; a 5xx is an unhandled failure.",
            evidence_ids=_ids(group),
        )

    failed = log.of_kind("request-failed")
    for host, group in _group(failed, lambda o: _host(o.data.get("url", ""))).items():
        first = group[0]
        reason = first.text.rsplit("failed: ", 1)[-1]
        if _host(first.page) == host:
            title = f"Request never completed: {_short(first.data.get('url', ''))} ({reason})"
            severity, confidence = "medium", "high"
            suggestion = (
                "Check that the endpoint is reachable, the certificate is valid, and nothing "
                "(CORS, mixed content, DNS) is blocking the request."
            )
        else:
            # A dead analytics or widget host is real but rarely what breaks the app.
            title = f"Third-party service unreachable: {host} ({reason})"
            severity, confidence = "low", "medium"
            suggestion = (
                "Remove or update the script that calls this host; if it is still needed, "
                "check its configuration. It costs a failed request on every page view."
            )
        collector.add(
            title=title,
            category="bug",
            severity=severity,
            confidence=confidence,
            kind="network",
            source="automated",
            location=_where(group),
            evidence="\n".join(o.text for o in group[:5]),
            steps=[f"Open {first.page}", "Open developer tools, Network tab"],
            suggestion=suggestion,
            evidence_ids=_ids(group),
        )


def _broken_links(log: ObservationLog, collector: FindingCollector) -> None:
    broken = log.of_kind("broken-link")
    if not broken:
        return
    listed = "\n".join(f"- {o.text} (linked from {_path(o.page)})" for o in broken[:15])
    collector.add(
        title=f"{len(broken)} broken internal link(s)",
        category="bug",
        severity="medium",
        confidence="high",
        kind="broken-link",
        source="automated",
        location=_where(broken),
        evidence=listed,
        steps=[f"Open {broken[0].page}", f"Follow the link to {broken[0].data.get('url')}"],
        suggestion="Update or remove these links, or add redirects if the pages moved.",
        evidence_ids=_ids(broken),
    )


_IMPACT = {"critical": "high", "serious": "medium", "moderate": "low", "minor": "low"}

# The accessibility rules sites fail most, in plain words: what is wrong, who it
# hurts, and what to change. A finding should make sense to someone who has never
# heard of WCAG. Any other rule falls back to axe-core's own description.
_A11Y_RULES: dict[str, tuple[str, str, str]] = {
    "color-contrast": (
        "Text is too faint against its background (low colour contrast)",
        "People with low vision, many older people and anyone reading a phone in sunlight "
        "struggle to read it.",
        "",  # built from the measured colours instead
    ),
    "image-alt": (
        "Images have no text alternative (alt text)",
        "Screen readers have nothing to say for them, and nothing shows if an image fails to load.",
        'Add alt text saying what each image shows, or alt="" when it is purely decorative.',
    ),
    "button-name": (
        "Buttons have no name a screen reader can announce",
        'Screen reader users hear only "button" and cannot tell what it does.',
        "Give each button visible text, an aria-label, or an image with alt text inside it.",
    ),
    "link-name": (
        "Links have no name a screen reader can announce",
        'Screen reader users hear only "link" and cannot tell where it goes.',
        "Give each link text, an aria-label, or alt text on the image inside it.",
    ),
    "label": (
        "Form fields have no label",
        "Screen reader users are not told what to enter, and clicking the text beside a field "
        "does not select it.",
        'Add a <label for="..."> for each field, or an aria-label.',
    ),
    "select-name": (
        "Dropdowns have no label",
        "Screen reader users are not told what the dropdown is for.",
        'Add a <label for="..."> for each dropdown, or an aria-label.',
    ),
    "input-image-alt": (
        "Image buttons have no text alternative",
        "Screen reader users cannot tell what the button does.",
        'Add alt text to each <input type="image"> saying what it does.',
    ),
    "frame-title": (
        "Embedded frames have no title",
        "Screen reader users cannot tell what an embedded map, video or form contains.",
        "Add a title attribute describing each <iframe>.",
    ),
    "meta-viewport": (
        "Zooming is blocked on phones",
        "People who need larger text cannot pinch to zoom.",
        "Remove user-scalable=no, and any maximum-scale below 5, from the viewport meta tag.",
    ),
    "link-in-text-block": (
        "Links in text stand out by colour alone",
        "People who cannot tell the colours apart cannot find the links.",
        "Underline links in body text, or make them contrast 3:1 with the text around them.",
    ),
    "aria-hidden-focus": (
        "Hidden content can still receive keyboard focus",
        "Keyboard users land on controls they cannot see, and screen readers stay silent.",
        "Make controls inside aria-hidden areas unfocusable, or remove aria-hidden.",
    ),
    "nested-interactive": (
        "Controls are nested inside other controls",
        "Screen readers and keyboards may skip the control inside.",
        "Do not put buttons or links inside other buttons or links.",
    ),
    "scrollable-region-focusable": (
        "Scrollable areas cannot be scrolled with the keyboard",
        "Keyboard users cannot reach the content inside them.",
        'Make the scrollable area focusable (tabindex="0") or put a focusable element inside it.',
    ),
    "bypass": (
        "There is no way to skip to the main content",
        "Keyboard users must tab through every menu link on every page.",
        'Add a "Skip to main content" link, or wrap the content in a <main> element.',
    ),
    "list": (
        "Lists are built incorrectly",
        "Screen readers announce the wrong number of items, or none.",
        "Put only <li> elements directly inside <ul> and <ol>.",
    ),
    "listitem": (
        "List items sit outside a list",
        "Screen readers do not announce them as a list.",
        "Wrap <li> elements in a <ul> or <ol>.",
    ),
}
_A11Y_FALLBACK_WHY = (
    "It makes the page harder to use for people who rely on assistive technology such as "
    "screen readers or keyboards."
)


def _accessibility(log: ObservationLog, collector: FindingCollector) -> None:
    for rule, group in _group(log.of_kind("a11y"), lambda o: o.data.get("id", o.text)).items():
        first = group[0]
        impact = first.data.get("impact") or "moderate"
        nodes = first.data.get("nodes") or []
        total = sum(int(o.data.get("count") or 0) for o in group)
        help_url = first.data.get("helpUrl")
        title, why, fix = _A11Y_RULES.get(
            rule,
            (
                first.data.get("help") or f"Accessibility rule {rule} failed",
                _A11Y_FALLBACK_WHY,
                f"See {help_url} for how to fix it.",
            ),
        )
        if rule == "color-contrast":
            pairs = [p for o in group for p in (o.data.get("pairs") or [])]
            evidence = _contrast_evidence(total, why, pairs, nodes)
            suggestion = _contrast_fix(pairs, nodes, help_url)
        else:
            evidence = (
                f"The automated accessibility check (axe-core) found {total} element(s) with "
                f"this problem. {why}\n\nRecorded evidence:\n"
                + "\n".join(f"- {_named(n)}{n['target']}: {n['html']}" for n in nodes[:3])
            )
            suggestion = fix
            if rule in _A11Y_RULES and help_url:  # the fallback's text already links it
                suggestion += f" Details: {help_url}"
        collector.add(
            title=title,
            category="bug" if impact == "critical" else "improvement",
            severity=_IMPACT.get(impact, "low"),
            confidence="high",
            kind="accessibility",
            source="automated",
            location=_where(group),
            selector=nodes[0]["target"] if nodes else None,
            evidence=evidence,
            steps=[f"Open {first.page}", "Run an accessibility checker such as axe DevTools"],
            suggestion=suggestion,
            evidence_ids=_ids(group),
        )


def _named(node: dict) -> str:
    """'"Book now" at ' when the element has visible text, else nothing."""
    return f'"{node["text"]}" at ' if node.get("text") else ""


def _contrast_evidence(total: int, why: str, pairs: list[dict], nodes: list[dict]) -> str:
    lines = [
        f"{p['fg']} text on {p['bg']}: {p['ratio']}:1, needs {p['need']} "
        f"({p['count']} element{'s' if p['count'] != 1 else ''})"
        for p in pairs[:5]
    ]
    for node in nodes[:3]:
        c = node.get("contrast")
        if c:
            # The text finds the element faster than a selector does; fall back to the selector.
            where = f'"{node["text"]}"' if node.get("text") else node["target"]
            lines.append(f"{where}: {c['fg']} on {c['bg']} = {c['ratio']}:1")
    return (
        f"{total} piece(s) of text are too faint against their background. Text needs a "
        "contrast ratio of at least 4.5:1, or 3:1 when it is large (about 24px, or 19px bold); "
        "1:1 would be invisible and black on white is 21:1. Text that is part of a logo is "
        f"exempt. {why}\n\nRecorded evidence:\n" + "\n".join(f"- {line}" for line in lines)
    )


def _contrast_fix(pairs: list[dict], nodes: list[dict], help_url: str | None) -> str:
    """One passing colour per failing pair: the most common pairs, then the named examples.

    The examples matter even when they are rare: the first failures on a page are often
    its header and its main button, which are the ones most worth fixing.
    """
    candidates = [
        (p["fg"], p["bg"], p["need"], f"{p['count']} element{'s' if p['count'] != 1 else ''}")
        for p in pairs[:3]
    ]
    for node in nodes[:3]:
        c = node.get("contrast")
        if c and node.get("text"):
            candidates.append((c["fg"], c["bg"], c["need"], f'"{node["text"]}"'))
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for fg, bg, need, label in candidates:
        if (fg, bg) in seen or len(lines) == 5:
            continue
        seen.add((fg, bg))
        if fix := passing_colour(fg, bg, need):
            lines.append(f"- {fix} ({label})")
    text = (
        "Darken the text or its background until each pair reaches the ratio it needs. "
        "The same colours, slightly darker, keep the design"
    )
    text += (":\n" + "\n".join(lines)) if lines else "."
    return text + (f"\nDetails: {help_url}" if help_url else "")


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _luminance(hex_colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    """The WCAG contrast ratio of two #rrggbb colours, from 1 to 21."""
    light, dark = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _darken_until(colour: str, against: str, target: float) -> str | None:
    """The same hue and saturation, darker, until it reaches `target` against `against`."""
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    while lightness > 0:
        rgb = colorsys.hls_to_rgb(hue, lightness, saturation)
        candidate = "#" + "".join(f"{round(c * 255):02x}" for c in rgb)
        if contrast_ratio(candidate, against) >= target:
            return candidate
        lightness -= 0.005
    return None


def passing_colour(fg: str, bg: str, need: str | float) -> str | None:
    """The smallest darkening that makes a text/background pair pass, as advice.

    Dark text on a light background: darken the text. Light text on a dark
    background (white on a brand-coloured button): darken the background, so the
    text stays as designed.
    """
    try:
        target = float(str(need).split(":")[0])
    except ValueError:
        return None
    if not (_HEX.match(fg or "") and _HEX.match(bg or "")):
        return None
    fg, bg = fg.lower(), bg.lower()
    if _luminance(fg) <= _luminance(bg):
        fixed = _darken_until(fg, bg, target)
        return f"text {fg} → {fixed} on {bg}" if fixed else None
    fixed = _darken_until(bg, fg, target)
    return f"background {bg} → {fixed} behind {fg} text" if fixed else None


def _performance(log: ObservationLog, collector: FindingCollector) -> None:
    for facts in log.of_kind("page-facts"):
        timing = facts.data.get("timing") or {}
        load = int(timing.get("load") or 0)
        size = int(timing.get("transferSize") or 0)
        if load >= SLUGGISH_LOAD_MS:
            collector.add(
                title=f"Slow page load: {load / 1000:.1f}s on {_path(facts.page)}",
                category="improvement",
                severity="medium" if load >= SLOW_LOAD_MS else "low",
                confidence="medium",  # one sample from one region, so indicative not conclusive
                kind="performance",
                source="automated",
                location=_path(facts.page),
                evidence=f"Load event fired at {load}ms (DOM ready at "
                f"{timing.get('domContentLoaded')}ms), measured once from the scanner.",
                steps=[f"Open {facts.page} with the cache disabled", "Check the Performance tab"],
                suggestion="Look for render-blocking scripts, large images and slow API calls "
                "on the critical path.",
                evidence_ids=[facts.id],
            )
        if size >= HEAVY_PAGE_BYTES:
            collector.add(
                title=f"Heavy page: {size / 1_000_000:.1f} MB document on {_path(facts.page)}",
                category="improvement",
                severity="low",
                confidence="high",
                kind="performance",
                source="automated",
                location=_path(facts.page),
                evidence=f"Navigation transferred {size} bytes.",
                steps=[f"Open {facts.page}", "Check transfer size in the Network tab"],
                suggestion="Compress and split what the first view does not need.",
                evidence_ids=[facts.id],
            )


def _metadata(log: ObservationLog, collector: FindingCollector) -> None:
    pages = log.of_kind("page-facts")
    checks = [
        (
            "viewport",
            "No mobile viewport meta tag",
            "medium",
            'Add <meta name="viewport" content="width=device-width, initial-scale=1"> or the '
            "page renders zoomed out on phones.",
        ),
        (
            "description",
            "No meta description",
            "low",
            "Add a one-sentence description; search engines and link previews show it.",
        ),
        (
            "lang",
            "Page language is not declared",
            "low",
            'Add lang="en" (or the right code) to <html> so screen readers pronounce text correctly.',
        ),
        ("title", "Page has no title", "medium", "Add a descriptive <title>."),
    ]
    for field, title, severity, suggestion in checks:
        missing = [p for p in pages if not p.data.get(field)]
        if not missing:
            continue
        collector.add(
            title=title + (f" on {len(missing)} pages" if len(missing) > 1 else ""),
            category="improvement",
            severity=severity,
            confidence="high",
            kind="accessibility" if field == "lang" else "seo",
            source="automated",
            location=_where(missing),
            evidence="Missing on: " + ", ".join(_path(p.page) for p in missing[:8]),
            steps=[f"Open {missing[0].page}", "View the page source and check <head>"],
            suggestion=suggestion,
            evidence_ids=_ids(missing),
        )

    no_h1 = [p for p in pages if int(p.data.get("h1Count") or 0) == 0]
    if no_h1:
        collector.add(
            title="Page has no main heading (h1)"
            + (f" on {len(no_h1)} pages" if len(no_h1) > 1 else ""),
            category="improvement",
            severity="low",
            confidence="high",
            kind="accessibility",
            source="automated",
            location=_where(no_h1),
            evidence="No <h1> found on: " + ", ".join(_path(p.page) for p in no_h1[:8]),
            steps=[f"Open {no_h1[0].page}", "Inspect the heading structure"],
            suggestion="Give each page one h1 describing it; screen reader users jump by headings.",
            evidence_ids=_ids(no_h1),
        )
