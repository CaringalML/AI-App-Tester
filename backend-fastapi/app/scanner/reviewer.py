"""Second-pass quality control over every finding.

The reviewer is the other half of false-positive handling. It sees each
finding next to the evidence it cites and can keep, merge, or drop it. Its
power is deliberately bounded in code, not just in the prompt:

- it can only act on findings that exist; it cannot add new ones
- high-confidence automated findings cannot be dropped, only adjusted
- dropped and merged findings are kept in `suppressed` with the reason, so the
  developer can see what was filtered and overrule it
"""

import logging
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..models import Finding, SuppressedFinding
from .observations import ObservationLog
from .prompts import REVIEW_SYSTEM
from .usage import UsageTracker

log = logging.getLogger(__name__)

_LEVELS = ["critical", "high", "medium", "low"]
_CONFIDENCE = ["high", "medium", "low"]

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["keep", "merge", "drop"]},
                    "merge_into": {"type": "string"},
                    "severity": {"type": "string", "enum": _LEVELS},
                    "confidence": {"type": "string", "enum": _CONFIDENCE},
                    "reason": {"type": "string"},
                },
                "required": [
                    "finding_id",
                    "action",
                    "merge_into",
                    "severity",
                    "confidence",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "decisions"],
    "additionalProperties": False,
}


class Decision(BaseModel):
    finding_id: str
    action: Literal["keep", "merge", "drop"]
    merge_into: str
    severity: Literal["critical", "high", "medium", "low"]
    confidence: Literal["high", "medium", "low"]
    reason: str


class Review(BaseModel):
    summary: str
    decisions: list[Decision]


def _describe(finding: Finding, observations: ObservationLog) -> str:
    grounded = bool(finding.evidence_ids)
    lines = [
        f"## {finding.id} ({finding.source}{'' if grounded else ', UNGROUNDED'})",
        f"{finding.category} / {finding.kind} / severity {finding.severity} / "
        f"confidence {finding.confidence}",
        f"Title: {finding.title}",
        f"Where: {finding.location}",
        f"Evidence: {finding.evidence[:900]}",
    ]
    for obs_id in finding.evidence_ids[:4]:
        observation = observations.get(obs_id)
        if observation:
            lines.append(f"Cited {observation.brief(260)}")
    return "\n".join(lines)


async def review_findings(
    *,
    client: anthropic.AsyncAnthropic,
    settings: Settings,
    findings: list[Finding],
    observations: ObservationLog,
    usage: UsageTracker,
    target_url: str,
    visited: list[str],
) -> tuple[list[Finding], list[SuppressedFinding], str | None]:
    if not findings:
        return [], [], None

    body = f"Site tested: {target_url}\nPages visited: {', '.join(visited[:10])}\n\n" + "\n\n".join(
        _describe(f, observations) for f in findings
    )
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=16_000,
        system=REVIEW_SYSTEM,
        messages=[{"role": "user", "content": body}],
        output_config={
            "effort": settings.review_effort,
            "format": {"type": "json_schema", "schema": REVIEW_SCHEMA},
        },
    )
    usage.add(response.usage)

    if response.stop_reason == "refusal":
        return findings, [], None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        review = Review.model_validate_json(text)
    except ValidationError as exc:
        log.warning("review output did not validate: %s", exc)
        return findings, [], None

    return (*apply_review(findings, review), review.summary.strip() or None)


def apply_review(
    findings: list[Finding], review: Review
) -> tuple[list[Finding], list[SuppressedFinding]]:
    by_id = {f.id: f for f in findings}
    decisions = {d.finding_id: d for d in review.decisions if d.finding_id in by_id}
    kept: list[Finding] = []
    suppressed: list[SuppressedFinding] = []

    for finding in findings:
        decision = decisions.get(finding.id)
        if decision is None:
            kept.append(finding)
            continue

        protected = finding.source == "automated" and finding.confidence == "high"
        target = by_id.get(decision.merge_into) if decision.action == "merge" else None

        if decision.action == "merge" and target is not None and target.id != finding.id:
            target.evidence_ids = list(dict.fromkeys(target.evidence_ids + finding.evidence_ids))
            suppressed.append(
                SuppressedFinding(
                    finding=finding, reason=f"Same issue as {target.id}. {decision.reason}".strip()
                )
            )
            continue
        if decision.action == "drop" and not protected:
            suppressed.append(SuppressedFinding(finding=finding, reason=decision.reason))
            continue

        finding.severity = decision.severity
        # The reviewer may lower confidence, but cannot promote an ungrounded claim.
        if finding.evidence_ids or _CONFIDENCE.index(decision.confidence) >= _CONFIDENCE.index(
            finding.confidence
        ):
            finding.confidence = decision.confidence
        kept.append(finding)

    return kept, suppressed
