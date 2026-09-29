"""Turns raw browser evidence into findings.

Everything here is deterministic. These findings are produced from things the
browser verifiably recorded, so they carry high confidence by construction and
are the half of the report that cannot be a hallucination.
"""

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


def _accessibility(log: ObservationLog, collector: FindingCollector) -> None:
    for rule, group in _group(log.of_kind("a11y"), lambda o: o.data.get("id", o.text)).items():
        first = group[0]
        impact = first.data.get("impact") or "moderate"
        nodes = first.data.get("nodes") or []
        examples = "\n".join(f"- {n['target']}: {n['html']}" for n in nodes[:3])
        total = sum(int(o.data.get("count") or 0) for o in group)
        collector.add(
            title=first.data.get("help") or f"Accessibility rule {rule} failed",
            category="bug" if impact == "critical" else "improvement",
            severity=_IMPACT.get(impact, "low"),
            confidence="high",
            kind="accessibility",
            source="automated",
            location=_where(group),
            selector=nodes[0]["target"] if nodes else None,
            evidence=f"axe-core rule '{rule}' ({impact} impact) failed on {total} element(s).\n"
            + examples,
            steps=[f"Open {first.page}", "Run an accessibility checker such as axe DevTools"],
            suggestion=f"See {first.data.get('helpUrl')} for the fix. This affects people using "
            "screen readers, keyboards or high contrast.",
            evidence_ids=_ids(group),
        )


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
