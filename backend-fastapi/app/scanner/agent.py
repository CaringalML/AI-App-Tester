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
from typing import Any

import anthropic

from ..artifacts import ArtifactStore
from ..config import Settings
from .browser import BrowserSession
from .findings import FindingCollector
from .observations import ObservationLog
from .prompts import AGENT_SYSTEM
from .usage import UsageTracker

log = logging.getLogger(__name__)

Progress = Callable[[str, str], Awaitable[None]]

_NO_ARGS = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_REF = {"type": "string", "description": "Element ref from get_page_state, e.g. e12."}

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
        "input_schema": _schema({"ref": _REF}),
    },
    {
        "name": "fill",
        "description": "Replace the value of an input or textarea. Use fake test data only.",
        "strict": True,
        "input_schema": _schema({"ref": _REF, "text": {"type": "string"}}),
    },
    {
        "name": "select_option",
        "description": "Choose an option in a <select> by its visible label or value.",
        "strict": True,
        "input_schema": _schema({"ref": _REF, "option": {"type": "string"}}),
    },
    {
        "name": "press_key",
        "description": "Press a key on the focused element, e.g. Enter to submit a form.",
        "strict": True,
        "input_schema": _schema(
            {"key": {"type": "string", "enum": ["Enter", "Tab", "Escape", "Space", "ArrowDown"]}}
        ),
    },
    {
        "name": "navigate",
        "description": "Go to a path or URL on the same site, e.g. /pricing.",
        "strict": True,
        "input_schema": _schema({"target": {"type": "string"}}),
    },
    {
        "name": "go_back",
        "description": "Press the browser back button.",
        "strict": True,
        "input_schema": _NO_ARGS,
    },
    {
        "name": "take_screenshot",
        "description": "See the current viewport. Use to judge layout, visual feedback and "
        "copy. Costs more than get_page_state, so use it when appearance matters.",
        "strict": True,
        "input_schema": _NO_ARGS,
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
        progress: Progress,
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
        self.progress = progress
        self.scan_id = scan_id
        self.deadline = deadline
        self.labels: dict[str, str] = {}
        self.steps = 0
        self.summary: str | None = None
        self.notes: list[str] = []

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

    async def _execute(self, name: str, args: dict[str, Any]) -> tuple[Any, bool, bool]:
        """Returns (tool_result content, is_error, finished)."""
        session = self.session
        try:
            match name:
                case "get_page_state":
                    state = await session.snapshot()
                    self._remember_labels(state)
                    return format_page_state(state), False, False
                case "click":
                    await self.progress(f"Clicking “{self._label(args['ref'])}”", "action")
                    return await session.click(args["ref"], self._label(args["ref"])), False, False
                case "fill":
                    label = self._label(args["ref"])
                    await self.progress(f"Typing “{args['text'][:40]}” into {label}", "action")
                    return await session.fill(args["ref"], label, args["text"]), False, False
                case "select_option":
                    label = self._label(args["ref"])
                    await self.progress(f"Choosing “{args['option']}” in {label}", "action")
                    result = await session.select(args["ref"], label, args["option"])
                    return result, False, False
                case "press_key":
                    await self.progress(f"Pressing {args['key']}", "action")
                    return await session.press(args["key"]), False, False
                case "navigate":
                    await self.progress(f"Opening {args['target']}", "action")
                    return await session.navigate(args["target"]), False, False
                case "go_back":
                    await self.progress("Going back", "action")
                    return await session.back(), False, False
                case "take_screenshot":
                    image = await session.screenshot()
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

        await self.progress(f"Found: {finding.title}", "finding")
        reply = f"Recorded {finding.id} with evidence {', '.join(valid) or 'none'}."
        if invalid:
            reply += f" Ignored unknown ids: {', '.join(invalid)}."
        return reply + note


def brief_automated(findings: list, limit: int = 25) -> str:
    if not findings:
        return "(none)"
    lines = [f"- {f.id} [{f.kind}] {f.title} at {f.location}" for f in findings[:limit]]
    if len(findings) > limit:
        lines.append(f"- … and {len(findings) - limit} more")
    return "\n".join(lines)
