from app.models import ScanOptions
from app.scanner.findings import FindingCollector, build_automated_findings
from app.scanner.observations import ObservationLog

PAGE = "https://shop.example/checkout"


def _log() -> ObservationLog:
    log = ObservationLog()
    log.add("page-error", PAGE, "TypeError: cart.items is undefined", stack="at pay (app.js:10)")
    log.add("page-error", PAGE, "TypeError: cart.items is undefined")
    log.add("console-error", PAGE, "Stripe key missing", source="app.js", line=4)
    log.add(
        "http-error",
        PAGE,
        "POST /api/pay returned HTTP 500",
        url="https://shop.example/api/pay",
        status=500,
        resource="fetch",
    )
    log.add(
        "http-error",
        PAGE,
        "GET /favicon.ico returned HTTP 404",
        url="https://shop.example/favicon.ico",
        status=404,
        resource="other",
    )
    log.add(
        "a11y",
        PAGE,
        "Buttons must have discernible text (2 element(s))",
        id="button-name",
        impact="critical",
        help="Buttons must have discernible text",
        helpUrl="https://dequeuniversity.com/rules/axe/4.10/button-name",
        count=2,
        nodes=[{"target": "#pay", "html": "<button id=pay></button>"}],
    )
    log.add(
        "page-facts",
        PAGE,
        "Audited",
        title="Checkout",
        description=None,
        viewport="width=device-width",
        lang=None,
        h1Count=1,
        timing={"load": 5200, "domContentLoaded": 3000, "transferSize": 1000},
    )
    return log


def _build(options: ScanOptions | None = None):
    collector = FindingCollector()
    build_automated_findings(_log(), collector, options or ScanOptions())
    return collector.items


def test_repeated_errors_are_grouped_and_cite_every_occurrence() -> None:
    crashes = [f for f in _build() if f.kind == "crash"]
    assert len(crashes) == 1
    assert crashes[0].severity == "high"
    assert crashes[0].confidence == "high"
    assert crashes[0].evidence_ids == ["obs-1", "obs-2"]
    assert "Thrown 2 time(s)" in crashes[0].evidence


def test_network_severity_depends_on_what_failed() -> None:
    network = {f.title: f for f in _build() if f.kind == "network"}
    server_error = next(f for t, f in network.items() if "500" in t)
    favicon = next(f for t, f in network.items() if "favicon" in t)
    assert server_error.severity == "high"
    assert favicon.severity == "low"


def test_critical_accessibility_violation_is_a_bug() -> None:
    a11y = next(f for f in _build() if f.title == "Buttons must have discernible text")
    assert a11y.category == "bug"
    assert a11y.severity == "high"
    assert a11y.selector == "#pay"


def test_slow_load_is_an_improvement_with_measured_evidence() -> None:
    slow = next(f for f in _build() if f.kind == "performance")
    assert slow.category == "improvement"
    assert slow.severity == "medium"
    assert "5200ms" in slow.evidence


def test_missing_metadata_is_reported() -> None:
    titles = [f.title for f in _build()]
    assert "No meta description" in titles
    assert "Page language is not declared" in titles
    assert "No mobile viewport meta tag" not in titles


def test_options_limit_what_is_reported() -> None:
    only_bugs = _build(ScanOptions(find_improvements=False, check_accessibility=False))
    assert only_bugs
    assert all(f.category == "bug" and f.kind != "accessibility" for f in only_bugs)
    assert not _build(
        ScanOptions(find_bugs=False, find_improvements=False, check_accessibility=False)
    )


def test_every_automated_finding_is_grounded() -> None:
    assert all(f.source == "automated" and f.evidence_ids for f in _build())


def test_dead_third_party_host_is_low_severity_with_a_readable_title() -> None:
    log = ObservationLog()
    beacon = "https://123.log.analytics.example/event?a=1&u=very-long-tracking-string"
    log.add("request-failed", PAGE, f"GET {beacon} failed: net::ERR_NAME_NOT_RESOLVED", url=beacon)
    own = "https://shop.example/api/cart?x=1"
    log.add("request-failed", PAGE, f"GET {own} failed: net::ERR_CONNECTION_RESET", url=own)
    collector = FindingCollector()
    build_automated_findings(log, collector, ScanOptions())
    by_title = {f.title: f for f in collector.items}

    third = by_title[
        "Third-party service unreachable: 123.log.analytics.example (net::ERR_NAME_NOT_RESOLVED)"
    ]
    assert (third.severity, third.confidence) == ("low", "medium")
    first_party = by_title["Request never completed: /api/cart (net::ERR_CONNECTION_RESET)"]
    assert first_party.severity == "medium"
