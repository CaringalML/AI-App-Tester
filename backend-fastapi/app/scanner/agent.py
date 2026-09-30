"""The exploration agent: Claude decides what to try, Playwright does it.

A manual tool-use loop rather than the SDK's tool runner, because the loop
needs things the runner does not expose: a step and wall-clock budget that
injects a wrap-up note, per-action progress events for the UI, and evidence
checking on every reported finding.

Built for Claude Opus 5.5:
- no `thinking` parameter (it is always on); `output_config.effort` is the dial
- `tool_choice` stays auto (forced tool use returns 400); tools are `strict`
- history is append-only and assistant content is passed back unmodified, so
  thinking blocks stay valid and the prompt cache prefix keeps hitting
"""

import base64
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import anthropic

from ..artifacts import ArtifactStore
from ..config import Settings
from .browser import ActionOutcome, BrowserSession
from .findings import FindingCollector
from .observations import ObservationLog
from .playwright_export import (
    EXPECTATION_TYPES,
    Expectation,
    TestAction,
    build_test,
    select_segment,
)
from .prompts import AGENT_SYSTEM
from .usage import UsageTracker

log = logging.getLogger(__name__)


class Recorder(Protocol):
    """Writes steps to the run's timeline; implemented by runner.Reporter."""

    async def step(self, kind: str, label: str, **fields: Any) -> None: ...
    async def advance(self, stage: str, completion: float) -> None: ...


_NO_ARGS = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_REF = {"type": "string", "description": "Element ref from get_page_state, e.g. e12."}
# Shown live to the person watching the run, like a narrated test. One short sentence.
_WHY = {
    "type": "string",
    "description": "One short sentence, shown live to the person watching, on what this "
    "action is checking. E.g. 'Checking that an empty form is rejected'.",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_page_state",
        "description": "Read the current page: URL, title, validation or alert messages, "
        "interactive elements with refs, and visible text. Call after navigating or when "
        "unsure what is on screen.",
        "strict": True,
        "input_schema": _NO_ARGS,
    },
    {
        "name": "click",
        "description": "Click an element. Returns what changed and any signals produced.",
        "strict": True,
        "input_schema": _schema({"ref": _REF, "why": _WHY}),
    },
    {
        "name": "fill",
        "description": "Replace the value of an input or textarea. Use fake test data only.",
        "strict": True,
        "input_schema": _schema({"ref": _REF, "text": {"type": "string"}, "why": _WHY}),
    },
    {
        "name": "select_option",
        "description": "Choose an option in a <select> by its visible label or value.",
        "strict": True,
        "input_schema": _schema({"ref": _REF, "option": {"type": "string"}, "why": _WHY}),
    },
    {
        "name": "press_key",
        "description": "Press a key on the focused element, e.g. Enter to submit a form.",
        "strict": True,
        "input_schema": _schema(
            {
                "key": {"type": "string", "enum": ["Enter", "Tab", "Escape", "Space", "ArrowDown"]},
                "why": _WHY,
            }
        ),
    },
    {
        "name": "navigate",
        "description": "Go to a path or URL on the same site, e.g. /pricing.",
        "strict": True,
        "input_schema": _schema({"target": {"type": "string"}, "why": _WHY}),
    },
    {
        "name": "go_back",
        "description": "Press the browser back button.",
        "strict": True,
        "input_schema": _schema({"why": _WHY}),
    },
    {
        "name": "take_screenshot",
        "description": "See the current viewport. Use to judge layout, visual feedback and "
        "copy. Costs more than get_page_state, so use it when appearance matters.",
        "strict": True,
        "input_schema": _schema({"why": _WHY}),
    },
    {
        "name": "report_finding",
        "description": "Record one confirmed issue. evidence_ids must list the act-N / obs-N "
        "ids from tool results that show it.",
        "strict": True,
        "input_schema": _schema(
            {
                "title": {"type": "string", "description": "One line, specific."},
                "category": {"type": "string", "enum": ["bug", "improvement"]},
                "kind": {
                    "type": "string",
                    "enum": [
                        "functional",
                        "ux",
                        "content",
                        "accessibility",
                        "performance",
                        "crash",
                        "console",
                        "network",
                    ],
                },
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "location": {"type": "string", "description": "Path where it happens."},
                "selector": {
                    "type": "string",
                    "description": "Element involved, as a ref or description. Empty if none.",
                },
                "evidence": {"type": "string", "description": "What you observed."},
                "steps": {"type": "array", "items": {"type": "string"}},
                "suggestion": {"type": "string", "description": "Concrete fix."},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
                "expectation": {
                    "type": "object",
                    "description": "What a user should observe once this is fixed, as one "
                    "checkable fact. It becomes the assertion of an exported Playwright "
                    "regression test, which must fail today and pass after the fix. Use "
                    "text for the text/url types, ref (from get_page_state) for element "
                    "types, and type none if no single check can express it.",
                    "properties": {
                        "type": {"type": "string", "enum": EXPECTATION_TYPES},
                        "text": {"type": "string"},
                        "ref": {"type": "string"},
                        "description": {
                            "type": "string",
                            "description": "The expected behaviour in one plain sentence.",
                        },
                    },
                    "required": ["type", "text", "ref", "description"],
                    "additionalProperties": False,
                },
            }
        ),
    },
    {
        "name": "finish",
        "description": "End the session with a short summary of what was tested.",
        "strict": True,
        "input_schema": _schema({"summary": {"type": "string"}}),
    },
]


def format_page_state(state: dict[str, Any]) -> str:
    lines = [f"URL: {state.get('url')}", f"Title: {state.get('title') or '(none)'}"]
    if state.get("alerts"):
        lines.append("Alerts / invalid fields: " + " | ".join(state["alerts"]))
    lines.append("Interactive elements:")
    for el in state.get("elements", []):
        desc = f"  {el['ref']} {el['tag']}"
        if el.get("type"):
            desc += f"[{el['type']}]"
        desc += f' "{el.get("label", "")}"'
        for key in ("href", "value"):
            if el.get(key):
                desc += f" {key}={el[key]}"
        if el.get("options"):
            desc += " options=" + "/".join(el["options"])
        if el.get("required"):
            desc += " required"
        if el.get("disabled"):
            desc += " disabled"
        lines.append(desc)
    lines.append("Visible text:")
    lines.append(state.get("text") or "(empty)")
    return "\n".join(lines)


class ExplorationAgent:
    def __init__(
        self,
        *,
        client: anthropic.AsyncAnthropic,
        settings: Settings,
        session: BrowserSession,
        observations: ObservationLog,
        collector: FindingCollector,
        artifacts: ArtifactStore,
        usage: UsageTracker,
        recorder: Recorder,
        scan_id: str,
        deadline: float,
    ) -> None:
        self.client = client
        self.settings = settings
        self.session = session
        self.observations = observations
        self.collector = collector
        self.artifacts = artifacts
        self.usage = usage
        self.recorder = recorder
        self.scan_id = scan_id
        self.deadline = deadline
        self.labels: dict[str, str] = {}
        self.steps = 0
        self.summary: str | None = None
        self.notes: list[str] = []
        # Replayable record of every browser action, for exported Playwright tests.
        self.test_actions: list[TestAction] = []

    async def run(self, automated_brief: str) -> None:
        state = await self.session.snapshot()
        self._remember_labels(state)
        kickoff = (
            f"Site under test: {self.session.page.url}\n"
            f"Action budget: {self.settings.max_agent_steps} tool calls.\n\n"
            f"Automated checks already recorded (do not re-report):\n{automated_brief}\n\n"
            f"Current page state:\n{format_page_state(state)}"
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": kickoff}]
        wrap_up_sent = False

        while True:
            response = await self.client.messages.create(
                model=self.settings.anthropic_model,
                max_tokens=16_000,
                system=AGENT_SYSTEM,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": self.settings.agent_effort},
                cache_control={"type": "ephemeral"},
            )
            self.usage.add(response.usage)
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                self.notes.append("The AI tester declined to continue on this site.")
                return
            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if response.stop_reason == "max_tokens" or not tool_uses:
                return

            results: list[dict[str, Any]] = []
            finished = False
            for tool_use in tool_uses:
                self.steps += 1
                content, is_error, done = await self._execute(tool_use.name, tool_use.input)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": content,
                        "is_error": is_error,
                    }
                )
                finished = finished or done
            budget = max(1, self.settings.max_agent_steps)
            await self.recorder.advance("exploring", 0.25 + 0.63 * min(1.0, self.steps / budget))

            if finished or self._exhausted():
                return

            remaining = self.settings.max_agent_steps - self.steps
            near_deadline = time.monotonic() > self.deadline - 45
            if not wrap_up_sent and (remaining <= 4 or near_deadline):
                # Appended after the tool results, never edited into earlier turns.
                results.append(
                    {
                        "type": "text",
                        "text": f"Budget note: about {max(remaining, 1)} action(s) left. "
                        "Wrap up now: report anything outstanding, then call finish.",
                    }
                )
                wrap_up_sent = True
            messages.append({"role": "user", "content": results})

    def _exhausted(self) -> bool:
        over_steps = self.steps >= self.settings.max_agent_steps + 2
        return over_steps or time.monotonic() > self.deadline

    def _remember_labels(self, state: dict[str, Any]) -> None:
        for element in state.get("elements", []):
            self.labels[element["ref"]] = element.get("label") or element["tag"]

    def _label(self, ref: str) -> str:
        return self.labels.get(ref, ref)

    async def _act(
        self,
        kind: str,
        label: str,
        why: str,
        run: Callable[[], Awaitable[ActionOutcome]],
        ref: str | None = None,
        *,
        test_kind: str,
        value: str = "",
    ) -> str:
        """Run one browser action and record it with before/after frames for the replay."""
        box, before, locator = None, None, None
        url_before = self.session.page.url
        if ref is not None:
            # Measured before acting: after a click the element may be gone.
            locator = await self.session.stable_locator(ref)
            box = await self.session.target_box(ref)
            before = await self._frame()
        outcome = await run()
        action = TestAction(
            act_id=outcome.id,
            kind=test_kind,
            url_before=url_before,
            url_after=outcome.url,
            label=label,
            value=outcome.url if test_kind == "goto" else value,
            locator=locator,
        )
        if not outcome.failed:
            self.test_actions.append(action)
        await self.recorder.step(
            kind,
            label,
            why=why.strip() or None,
            url=outcome.url,
            shot=await self._frame(),
            before=before,
            box=box,
            status="failed" if outcome.failed else "ok",
            action_id=outcome.id,
            signals=outcome.signals,
            code=action.code(),
        )
        return outcome.for_model()

    async def _frame(self) -> bytes | None:
        try:
            return await self.session.screenshot()
        except Exception:  # a frame is for the viewer; never let it break the action
            return None

    async def _execute(self, name: str, args: dict[str, Any]) -> tuple[Any, bool, bool]:
        """Returns (tool_result content, is_error, finished)."""
        session = self.session
        why = args.get("why", "")
        try:
            match name:
                case "get_page_state":
                    state = await session.snapshot()
                    self._remember_labels(state)
                    await self.recorder.step("read", "Read the page", url=state.get("url"))
                    return format_page_state(state), False, False
                case "click":
                    ref = args["ref"]
                    label = self._label(ref)
                    result = await self._act(
                        "click",
                        f"“{label}”",
                        why,
                        lambda: session.click(ref, label),
                        ref,
                        test_kind="click",
                    )
                    return result, False, False
                case "fill":
                    ref, text = args["ref"], args["text"]
                    label = self._label(ref)
                    result = await self._act(
                        "type",
                        f"“{text[:60]}” into {label}",
                        why,
                        lambda: session.fill(ref, label, text),
                        ref,
                        test_kind="fill",
                        value=text,
                    )
                    return result, False, False
                case "select_option":
                    ref, option = args["ref"], args["option"]
                    label = self._label(ref)
                    result = await self._act(
                        "select",
                        f"“{option}” in {label}",
                        why,
                        lambda: session.select(ref, label, option),
                        ref,
                        test_kind="select",
                        value=option,
                    )
                    return result, False, False
                case "press_key":
                    key = args["key"]
                    result = await self._act(
                        "press", key, why, lambda: session.press(key), test_kind="press", value=key
                    )
                    return result, False, False
                case "navigate":
                    target = args["target"]
                    result = await self._act(
                        "navigate", target, why, lambda: session.navigate(target), test_kind="goto"
                    )
                    return result, False, False
                case "go_back":
                    result = await self._act("back", "Back", why, session.back, test_kind="back")
                    return result, False, False
                case "take_screenshot":
                    image = await session.screenshot()
                    await self.recorder.step(
                        "look",
                        "Looked at the page",
                        why=why or None,
                        url=session.page.url,
                        shot=image,
                    )
                    return (
                        [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": base64.b64encode(image).decode(),
                                },
                            },
                            {"type": "text", "text": f"Screenshot of {session.page.url}"},
                        ],
                        False,
                        False,
                    )
                case "report_finding":
                    return await self._report(args), False, False
                case "finish":
                    self.summary = args["summary"].strip()
                    await self.recorder.step("stage", "Claude finished exploring", status="info")
                    return "Session ended.", False, True
                case _:
                    return f"Unknown tool {name}", True, False
        except (ValueError, KeyError) as exc:
            return f"Error: {exc}", True, False
        except Exception as exc:  # a browser failure should not end the whole scan
            log.warning("tool %s failed: %s", name, exc)
            return f"Error: {type(exc).__name__}: {str(exc)[:200]}", True, False

    async def _report(self, args: dict[str, Any]) -> str:
        cited = [i.strip() for i in args["evidence_ids"] if i.strip()]
        valid = [i for i in cited if i in self.observations]
        invalid = [i for i in cited if i not in self.observations]

        confidence = args["confidence"]
        note = ""
        if not valid:
            # The model's opinion alone is not evidence. Keep it visible, but say so.
            confidence = "low"
            note = (
                " None of the cited ids exist, so it was recorded as low confidence and "
                "flagged as ungrounded. Cite act-N / obs-N ids from tool results."
            )

        evidence = args["evidence"].strip()
        if valid:
            excerpts = [self.observations.get(i).brief(200) for i in valid[:4]]
            evidence += "\n\nRecorded evidence:\n" + "\n".join(f"- {e}" for e in excerpts)

        finding = self.collector.add(
            title=args["title"].strip()[:160],
            category=args["category"],
            severity=args["severity"],
            confidence=confidence,
            kind=args["kind"],
            source="agent",
            location=args["location"].strip() or self.session.page.url,
            selector=args["selector"].strip() or None,
            evidence=evidence,
            steps=[s.strip() for s in args["steps"] if s.strip()],
            suggestion=args["suggestion"].strip(),
            evidence_ids=valid,
        )
        try:
            key = f"{self.scan_id}/{finding.id}.jpg"
            await self.artifacts.save_jpeg(key, await self.session.screenshot())
            finding.screenshot_key = key
        except Exception as exc:  # evidence image is a bonus, not a requirement
            log.warning("screenshot for %s failed: %s", finding.id, exc)

        test_line = await self._attach_test(finding, args.get("expectation") or {}, valid)

        await self.recorder.step(
            "finding",
            finding.title,
            url=self.session.page.url,
            status="finding",
            finding_id=finding.id,
            shot_key=finding.screenshot_key,
        )
        reply = f"Recorded {finding.id} with evidence {', '.join(valid) or 'none'}."
        reply += test_line
        if invalid:
            reply += f" Ignored unknown ids: {', '.join(invalid)}."
        return reply + note

    async def _attach_test(self, finding: Any, raw: dict[str, Any], cited: list[str]) -> str:
        """Build the finding's Playwright test and check it against the page now."""
        session = self.session
        kind = raw.get("type") or "none"
        ref = (raw.get("ref") or "").strip()
        locator = await session.stable_locator(ref) if ref and kind.startswith("element_") else None
        expectation = Expectation(
            type=kind,
            text=raw.get("text") or "",
            description=(raw.get("description") or "").strip(),
            locator=locator,
        )
        segment = select_segment(self.test_actions, cited)
        start_url = segment[0].url_before if segment else session.page.url

        replayable = all(action.replayable for action in segment)
        holds: bool | None = None
        reason = ""
        if expectation.checkable and replayable:
            holds, reason = await session.replay_holds(
                start_url, segment, kind, expectation.text, locator
            )

        if not expectation.checkable:
            status, note = (
                "unverified",
                (
                    "No single check could express the fix, so the test replays the steps and "
                    "leaves the assertion as a TODO."
                ),
            )
        elif not replayable:
            status, note = (
                "unverified",
                ("Some steps had no unique locator; they are left as TODO comments."),
            )
        elif holds is None:
            status = "unverified"
            note = "The steps could not be replayed to check the assertion"
            note += f" ({reason})." if reason else "."
        elif holds:
            status, note = (
                "passes-now",
                (
                    "Replayed in a fresh browser before export: the assertion already holds, so "
                    "this test may not catch the issue. Review the expectation."
                ),
            )
        else:
            status, note = (
                "fails-now",
                (
                    "Replayed in a fresh browser before export: the assertion fails today, so the "
                    "test catches the issue and should pass once it is fixed."
                ),
            )

        test = build_test(
            title=finding.title,
            start_url=start_url,
            actions=segment,
            expectation=expectation,
            status=status,
            note=note,
        )
        finding.playwright_test = test.code
        finding.test_status = test.status
        finding.test_note = test.note
        finding.test_body = test.body
        return {
            "fails-now": " Exported a Playwright test; replayed in a fresh browser, its assertion "
            "fails today, as a regression test should.",
            "passes-now": " Exported a Playwright test, but its assertion already holds on the "
            "page, so it may not catch this. The expectation should describe the fixed "
            "behaviour, which the page does not show yet.",
            "unverified": " Exported a Playwright test; it could not be checked automatically.",
        }[test.status]


def brief_automated(findings: list, limit: int = 25) -> str:
    if not findings:
        return "(none)"
    lines = [f"- {f.id} [{f.kind}] {f.title} at {f.location}" for f in findings[:limit]]
    if len(findings) > limit:
        lines.append(f"- … and {len(findings) - limit} more")
    return "\n".join(lines)
