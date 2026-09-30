"""Turn a finding into a Playwright regression test a developer can keep.

The code is assembled here, never written by the model:

- Steps come from what the browser actually did during the scan, with each
  element addressed by a locator that was checked, with Playwright itself, to
  match exactly one element on the real page (see BrowserSession.stable_locator).
- The assertion comes from a structured `expectation` Claude attaches to the
  finding: one checkable fact from a fixed set, such as "this text should be
  visible". The server renders it to `expect(...)`.

Model output and page text only ever land in two places: string literals, via
`js()`, and single-line comments, via `_comment()`, which removes every line
terminator JavaScript recognises. Nothing can end the literal or the comment it
sits in, so a page cannot prompt-inject executable code into the exported test.

A test asserts the *correct* behaviour, so it fails while the bug exists and
passes once it is fixed. Where the expectation can be checked against the page
at the moment of the report, `test_status` records whether it fails today.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

TestStatus = Literal["fails-now", "passes-now", "unverified"]

EXPECTATION_TYPES = [
    "text_visible",
    "text_not_visible",
    "url_contains",
    "url_not_contains",
    "element_visible",
    "element_hidden",
    "element_text",
    "no_console_errors",
    "none",
]


def js(value: str) -> str:
    """A JavaScript string literal. JSON strings are valid JS string literals.

    U+2028 and U+2029 are escaped too: JSON allows them raw, but JavaScript
    treats them as line breaks, which matters wherever a literal sits in a comment.
    """
    literal = json.dumps(value, ensure_ascii=False)
    return literal.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def js_regex_source(text: str) -> str:
    """Escape text for use inside `new RegExp(...)`."""
    return re.sub(r"[.*+?^${}()|[\]\\/]", lambda m: "\\" + m.group(0), text)


def render_locator(desc: dict[str, Any] | None) -> str | None:
    if not desc:
        return None
    kind = desc.get("kind")
    if kind == "role":
        exact = ", exact: true" if desc.get("exact", True) else ""
        return f"page.getByRole({js(desc['role'])}, {{ name: {js(desc['name'])}{exact} }})"
    if kind == "label":
        return f"page.getByLabel({js(desc['value'])}, {{ exact: true }})"
    if kind == "placeholder":
        return f"page.getByPlaceholder({js(desc['value'])}, {{ exact: true }})"
    if kind == "testid":
        return f"page.getByTestId({js(desc['value'])})"
    if kind == "text":
        return f"page.getByText({js(desc['value'])}, {{ exact: true }})"
    if kind == "css":
        return f"page.locator({js(desc['value'])})"
    return None


@dataclass
class TestAction:
    """One replayable browser action from the scan."""

    __test__ = False  # its name starts with Test; tell pytest it is not a test class

    act_id: str
    kind: Literal["click", "fill", "select", "press", "goto", "back"]
    url_before: str
    url_after: str
    label: str = ""
    value: str = ""
    locator: dict[str, Any] | None = None

    def code(self) -> str:
        target = render_locator(self.locator)
        match self.kind:
            case "goto":
                return f"await page.goto({js(self.value)});"
            case "back":
                return "await page.goBack();"
            case "press":
                return f"await page.keyboard.press({js(self.value)});"
            case "click" if target:
                return f"await {target}.click();"
            case "fill" if target:
                return f"await {target}.fill({js(self.value)});"
            case "select" if target:
                return f"await {target}.selectOption({js(self.value)});"
        # No locator matched exactly one element; say so instead of guessing.
        return f"// TODO: {self.kind} {_comment(self.label)}: no unique locator was found for it"

    @property
    def replayable(self) -> bool:
        return not self.code().startswith("// TODO")


@dataclass
class Expectation:
    type: str
    text: str = ""
    description: str = ""
    locator: dict[str, Any] | None = None

    def lines(self) -> list[str]:
        """The assertion, or a TODO when the expectation cannot be expressed."""
        target = render_locator(self.locator)
        text = self.text.strip()
        match self.type:
            case "text_visible" if text:
                return [f"await expect(page.getByText({js(text)})).toBeVisible();"]
            case "text_not_visible" if text:
                return [f"await expect(page.getByText({js(text)})).toHaveCount(0);"]
            case "url_contains" if text:
                return [f"await expect(page).toHaveURL(new RegExp({js(js_regex_source(text))}));"]
            case "url_not_contains" if text:
                pattern = js(js_regex_source(text))
                return [f"await expect(page).not.toHaveURL(new RegExp({pattern}));"]
            case "element_visible" if target:
                return [f"await expect({target}).toBeVisible();"]
            case "element_hidden" if target:
                return [f"await expect({target}).toBeHidden();"]
            case "element_text" if target and text:
                return [f"await expect({target}).toContainText({js(text)});"]
            case "no_console_errors":
                return ["expect(errors).toEqual([]);"]
        return [f"// TODO: assert that {_comment(self.description or 'the issue is fixed')}"]

    @property
    def checkable(self) -> bool:
        return not self.lines()[0].startswith("// TODO")


@dataclass
class GeneratedTest:
    code: str
    status: TestStatus
    note: str
    body: list[str] = field(default_factory=list)


def _comment(text: str) -> str:
    """Text safe inside a `//` comment: one line, whatever it contained.

    str.split() breaks on every JavaScript line terminator (\\n, \\r, U+2028,
    U+2029), so none survive to end the comment early.
    """
    return " ".join(text.split())[:200]


def build_test(
    *,
    title: str,
    start_url: str,
    actions: list[TestAction],
    expectation: Expectation,
    status: TestStatus,
    note: str,
) -> GeneratedTest:
    body: list[str] = []
    if expectation.type == "no_console_errors":
        body += [
            "const errors: string[] = [];",
            "page.on('pageerror', (error) => errors.push(error.message));",
            "page.on('console', (message) => {",
            "  if (message.type() === 'error') errors.push(message.text());",
            "});",
            "",
        ]
    body.append(f"await page.goto({js(start_url)});")
    body += [action.code() for action in actions]
    if expectation.description:
        body.append(f"// Expected once fixed: {_comment(expectation.description)}")
    body += expectation.lines()
    return GeneratedTest(code=render_file([(title, body)]), status=status, note=note, body=body)


def render_file(tests: list[tuple[str, list[str]]]) -> str:
    lines = [
        "import { test, expect } from '@playwright/test';",
        "",
        "// Generated by AI App Tester from a recorded run. Each test asserts the correct",
        "// behaviour, so it fails while the issue exists and passes once it is fixed.",
    ]
    for title, body in tests:
        lines += ["", f"test({js(title)}, async ({{ page }}) => {{"]
        lines += [f"  {line}" if line else "" for line in body]
        lines.append("});")
    return "\n".join(lines) + "\n"


def automated_test(finding_kind: str, page: str, data: dict[str, Any]) -> tuple[list[str], str]:
    """Tests for browser-verified findings, which need no model input at all."""
    if finding_kind in ("crash", "console"):
        return (
            [
                "const errors: string[] = [];",
                "page.on('pageerror', (error) => errors.push(error.message));",
                "page.on('console', (message) => {",
                "  if (message.type() === 'error') errors.push(message.text());",
                "});",
                "",
                f"await page.goto({js(page)});",
                "await page.waitForLoadState('networkidle');",
                "expect(errors).toEqual([]);",
            ],
            "Loads the page and fails on any uncaught error or console error.",
        )
    if finding_kind == "network" and data.get("url") and data.get("status"):
        return (
            [
                "const failures: string[] = [];",
                "page.on('response', (response) => {",
                f"  if (response.url() === {js(data['url'])} && response.status() >= 400) {{",
                "    failures.push(`${response.status()} ${response.url()}`);",
                "  }",
                "});",
                "",
                f"await page.goto({js(page)});",
                "await page.waitForLoadState('networkidle');",
                "expect(failures).toEqual([]);",
            ],
            "Loads the page and fails if that request still returns an error status.",
        )
    if finding_kind == "broken-link" and data.get("url"):
        return (
            [
                f"const response = await page.request.get({js(data['url'])});",
                "expect(response.ok()).toBeTruthy();",
            ],
            "Requests the linked URL and fails while it returns an error status.",
        )
    return ([], "")


def select_segment(actions: list[TestAction], cited: list[str]) -> list[TestAction]:
    """Which recorded actions a finding's test should replay.

    Start from the first action the finding cites (or the last one taken, when
    it cites none), then walk back over the fields filled in just before it on
    the same page, so a submit gets the input it depended on. An earlier click
    or key press ends the walk: that was a separate attempt, not setup. End at the last cited
    action: anything the tester did after that is a different experiment, and
    replaying it would change the state the assertion describes.
    """
    if not actions:
        return []
    ids = [a.act_id for a in actions]
    cited_positions = [ids.index(c) for c in cited if c in ids]
    anchor = min(cited_positions) if cited_positions else len(actions) - 1
    start = anchor
    page = _page(actions[anchor].url_before)
    while start > 0:
        previous = actions[start - 1]
        same_page = _page(previous.url_before) == page and _page(previous.url_after) == page
        if not same_page or previous.kind not in ("fill", "select"):
            break
        start -= 1
    end = max(cited_positions) + 1 if cited_positions else len(actions)
    return actions[start:end]


def _page(url: str) -> str:
    return url.split("#")[0].split("?")[0].rstrip("/")


def attach_automated_tests(findings: list, observations) -> None:  # noqa: ANN001
    """Give browser-verified findings a test too. No model input is involved."""
    for finding in findings:
        if finding.source != "automated" or not finding.evidence_ids:
            continue
        first = observations.get(finding.evidence_ids[0])
        if first is None:
            continue
        body, note = automated_test(finding.kind, first.page, first.data)
        if not body:
            continue
        finding.test_body = body
        finding.playwright_test = render_file([(finding.title, body)])
        finding.test_status = "fails-now"
        finding.test_note = f"The browser recorded this during the scan. {note}"


def render_suite(findings: list) -> str | None:  # noqa: ANN001
    """Every finding's test in one spec file, titled by finding id so names stay unique."""
    tests = [(f"{f.id}: {f.title}", f.test_body) for f in findings if f.test_body]
    return render_file(tests) if tests else None
