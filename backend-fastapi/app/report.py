"""Markdown export, shaped to paste straight into a GitHub issue or a ticket."""

from .models import SEVERITY_ORDER, Finding, Scan


def _finding_md(finding: Finding) -> str:
    source = "found by browser checks" if finding.source == "automated" else "found by AI tester"
    lines = [
        f"### [{finding.severity.upper()}] {finding.title}",
        "",
        f"- **Where:** `{finding.location}`"
        + (f" (`{finding.selector}`)" if finding.selector else ""),
        f"- **Type:** {finding.category}, {finding.kind}, {source}",
        f"- **Confidence:** {finding.confidence}",
        "",
        "**Steps to reproduce**",
        "",
        *[f"{i}. {step}" for i, step in enumerate(finding.steps, start=1)],
        "",
        "**What happened**",
        "",
        "```text",
        finding.evidence,
        "```",
        "",
        f"**Suggested fix:** {finding.suggestion}",
    ]
    if finding.playwright_test:
        verdict = {
            "fails-now": "fails today, as it should",
            "passes-now": "already passes, so it may not catch this",
            "unverified": "not checked automatically",
        }.get(finding.test_status or "unverified", "")
        lines += [
            "",
            f"**Regression test** (Playwright, {verdict})",
            "",
            "```ts",
            finding.playwright_test.rstrip(),
            "```",
        ]
    if finding.screenshot_url:
        lines += ["", f"[Screenshot at the time of the finding]({finding.screenshot_url})"]
    return "\n".join(lines)


def render_markdown(scan: Scan) -> str:
    findings = sorted(scan.findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.category))
    bugs = [f for f in findings if f.category == "bug"]
    improvements = [f for f in findings if f.category == "improvement"]

    out = [
        f"# AI App Tester report: {scan.target_url}",
        "",
        f"Scanned {scan.finished_at or scan.created_at:%Y-%m-%d %H:%M} UTC across "
        f"{len(scan.visited_urls)} page(s). "
        f"{len(bugs)} bug(s), {len(improvements)} improvement(s).",
        "",
    ]
    if scan.summary:
        out += ["## Summary", "", scan.summary, ""]
    if bugs:
        out += ["## Broken", ""] + [_finding_md(f) + "\n" for f in bugs]
    if improvements:
        out += ["## Could be better", ""] + [_finding_md(f) + "\n" for f in improvements]
    if scan.suppressed:
        out += ["## Filtered out as likely noise", ""]
        out += [f"- {s.finding.title}: {s.reason}" for s in scan.suppressed]
        out.append("")
    cost = scan.usage.estimated_cost_usd
    out += [
        "---",
        f"_Model {scan.model}, {scan.usage.requests} API calls, "
        f"{scan.agent_steps} browser actions"
        + (f", about ${cost:.2f}" if cost is not None else "")
        + "._",
    ]
    return "\n".join(out)
