from app.models import ScanOptions
from app.scanner.findings import (
    FindingCollector,
    build_automated_findings,
    contrast_ratio,
    passing_colour,
)
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
    a11y = next(f for f in _build() if f.kind == "accessibility" and f.selector == "#pay")
    assert a11y.category == "bug"
    assert a11y.severity == "high"
    # Said in plain words, with who it affects, rather than quoting the rule.
    assert a11y.title == "Buttons have no name a screen reader can announce"
    assert 'hear only "button"' in a11y.evidence
    assert a11y.suggestion.endswith("https://dequeuniversity.com/rules/axe/4.10/button-name")


# Measured by axe-core on a real spa website, 30 Sep 2026.
_CONTRAST = {
    "id": "color-contrast",
    "impact": "serious",
    "help": "Elements must meet minimum color contrast ratio thresholds",
    "helpUrl": "https://dequeuniversity.com/rules/axe/4.10/color-contrast",
    "count": 172,
    "nodes": [
        {
            "target": ".btn",
            "html": '<a class="btn">Book now</a>',
            "text": "Book now",
            "contrast": {"fg": "#ffffff", "bg": "#887564", "ratio": 4.39, "need": "4.5:1"},
        }
    ],
    "pairs": [
        {"fg": "#a48d78", "bg": "#e6dac8", "ratio": 2.28, "need": "4.5:1", "count": 36},
        {"fg": "#ffffff", "bg": "#887564", "ratio": 4.39, "need": "4.5:1", "count": 3},
    ],
}


def _contrast_finding():
    log = ObservationLog()
    log.add("a11y", PAGE, "contrast", **_CONTRAST)
    collector = FindingCollector()
    build_automated_findings(log, collector, ScanOptions())
    return collector.items[0]


def test_contrast_finding_shows_the_measured_colours_in_plain_words() -> None:
    finding = _contrast_finding()
    assert finding.title == "Text is too faint against its background (low colour contrast)"
    assert finding.category == "improvement" and finding.severity == "medium"
    assert "172 piece(s) of text are too faint" in finding.evidence
    assert "at least 4.5:1" in finding.evidence
    assert "- #a48d78 text on #e6dac8: 2.28:1, needs 4.5:1 (36 elements)" in finding.evidence
    assert '- "Book now": #ffffff on #887564 = 4.39:1' in finding.evidence
    assert "logo is exempt" in finding.evidence


def test_contrast_fix_suggests_the_nearest_colours_that_pass() -> None:
    suggestion = _contrast_finding().suggestion
    # Dark text on a light background: darken the text.
    assert "\n- text #a48d78 → #705d4b on #e6dac8 (36 elements)" in suggestion
    # White text on a coloured button: darken the button, keep the text. Listed once,
    # though the pair also appears as the named "Book now" example.
    assert "\n- background #887564 → #857262 behind #ffffff text (3 elements)" in suggestion
    assert suggestion.count("#857262") == 1
    assert suggestion.endswith(
        "\nDetails: https://dequeuniversity.com/rules/axe/4.10/color-contrast"
    )


def test_a_rare_pair_on_a_named_element_still_gets_a_fix() -> None:
    # The page's main button is often a pair of its own, used once; it must not drop out.
    data = {**_CONTRAST, "pairs": _CONTRAST["pairs"][:1]}
    log = ObservationLog()
    log.add("a11y", PAGE, "contrast", **data)
    collector = FindingCollector()
    build_automated_findings(log, collector, ScanOptions())
    assert '- background #887564 → #857262 behind #ffffff text ("Book now")' in (
        collector.items[0].suggestion
    )
    assert contrast_ratio("#705d4b", "#e6dac8") >= 4.5
    assert contrast_ratio("#ffffff", "#857262") >= 4.5


def test_contrast_ratio_matches_the_wcag_formula() -> None:
    assert round(contrast_ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast_ratio("#777777", "#777777"), 1) == 1.0
    assert passing_colour("#a48d78", "not-a-colour", "4.5:1") is None


def test_an_unfamiliar_rule_falls_back_to_axe_cores_words() -> None:
    log = ObservationLog()
    log.add(
        "a11y",
        PAGE,
        "x",
        id="some-new-rule",
        impact="moderate",
        help="Some new rule text",
        helpUrl="https://dequeuniversity.com/rules/axe/4.10/some-new-rule",
        count=1,
        nodes=[{"target": "#x", "html": "<div id=x></div>"}],
    )
    collector = FindingCollector()
    build_automated_findings(log, collector, ScanOptions())
    finding = next(f for f in collector.items if f.kind == "accessibility")
    assert finding.title == "Some new rule text"
    assert finding.suggestion == (
        "See https://dequeuniversity.com/rules/axe/4.10/some-new-rule for how to fix it."
    )


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
