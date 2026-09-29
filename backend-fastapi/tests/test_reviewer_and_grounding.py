from types import SimpleNamespace

from app.config import Settings
from app.models import Finding
from app.scanner.agent import ExplorationAgent
from app.scanner.findings import FindingCollector
from app.scanner.observations import ObservationLog
from app.scanner.reviewer import Decision, Review, apply_review
from app.scanner.usage import UsageTracker


def _finding(fid: str, source: str = "agent", confidence: str = "medium", ids=None) -> Finding:
    return Finding(
        id=fid,
        title=f"Issue {fid}",
        category="bug",
        severity="medium",
        confidence=confidence,
        kind="functional",
        source=source,
        location="/",
        evidence="seen",
        steps=["open"],
        suggestion="fix",
        evidence_ids=ids if ids is not None else ["act-1"],
    )


def _decide(fid: str, action: str, **kw) -> Decision:
    return Decision(
        finding_id=fid,
        action=action,
        merge_into=kw.get("merge_into", ""),
        severity=kw.get("severity", "medium"),
        confidence=kw.get("confidence", "medium"),
        reason=kw.get("reason", "r"),
    )


def test_reviewer_can_drop_agent_noise_but_it_stays_visible() -> None:
    kept, suppressed = apply_review(
        [_finding("f-1"), _finding("f-2")],
        Review(summary="s", decisions=[_decide("f-2", "drop", reason="expected 401")]),
    )
    assert [f.id for f in kept] == ["f-1"]
    assert suppressed[0].finding.id == "f-2"
    assert suppressed[0].reason == "expected 401"


def test_reviewer_cannot_drop_what_the_browser_recorded() -> None:
    automated = _finding("f-1", source="automated", confidence="high")
    kept, suppressed = apply_review(
        [automated], Review(summary="s", decisions=[_decide("f-1", "drop", confidence="high")])
    )
    assert [f.id for f in kept] == ["f-1"]
    assert not suppressed


def test_merge_moves_evidence_to_the_survivor() -> None:
    a, b = _finding("f-1", ids=["obs-1"]), _finding("f-2", ids=["obs-2"])
    kept, suppressed = apply_review(
        [a, b], Review(summary="s", decisions=[_decide("f-2", "merge", merge_into="f-1")])
    )
    assert [f.id for f in kept] == ["f-1"]
    assert kept[0].evidence_ids == ["obs-1", "obs-2"]
    assert "Same issue as f-1" in suppressed[0].reason


def test_reviewer_cannot_promote_an_ungrounded_claim() -> None:
    ungrounded = _finding("f-1", confidence="low", ids=[])
    kept, _ = apply_review(
        [ungrounded], Review(summary="s", decisions=[_decide("f-1", "keep", confidence="high")])
    )
    assert kept[0].confidence == "low"


def test_reviewer_ignores_invented_ids() -> None:
    kept, suppressed = apply_review(
        [_finding("f-1")], Review(summary="s", decisions=[_decide("f-99", "drop")])
    )
    assert [f.id for f in kept] == ["f-1"] and not suppressed


class _FakeSession:
    page = SimpleNamespace(url="https://app.example/signup")

    async def screenshot(self) -> bytes:
        return b"jpeg"


class _FakeArtifacts:
    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}

    async def save_jpeg(self, key: str, data: bytes) -> None:
        self.saved[key] = data

    async def url_for(self, key: str) -> str:
        return key


def _agent(observations: ObservationLog, collector: FindingCollector) -> ExplorationAgent:
    async def progress(_message: str, _kind: str = "info") -> None:
        return None

    return ExplorationAgent(
        client=None,
        settings=Settings(),
        session=_FakeSession(),
        observations=observations,
        collector=collector,
        artifacts=_FakeArtifacts(),
        usage=UsageTracker("claude-opus-5-5"),
        progress=progress,
        scan_id="abc",
        deadline=0,
    )


def _args(ids: list[str], confidence: str = "high") -> dict:
    return {
        "title": "Sign-up accepts an empty password",
        "category": "bug",
        "kind": "functional",
        "severity": "high",
        "confidence": confidence,
        "location": "/signup",
        "selector": "",
        "evidence": "Submitted with no password and got a 500.",
        "steps": ["Open /signup", "Submit"],
        "suggestion": "Validate the field.",
        "evidence_ids": ids,
    }


async def test_grounded_finding_keeps_confidence_and_quotes_evidence() -> None:
    observations, collector = ObservationLog(), FindingCollector()
    action = observations.add("action", "/signup", "clicked e4 | 1 new signal: HTTP 500")
    reply = await _agent(observations, collector)._report(_args([action.id]))
    finding = collector.items[0]
    assert finding.confidence == "high"
    assert finding.evidence_ids == ["act-1"]
    assert "Recorded evidence" in finding.evidence
    assert finding.screenshot_key == "abc/f-1.jpg"
    assert "Recorded f-1" in reply


async def test_ungrounded_finding_is_downgraded_and_the_model_is_told() -> None:
    observations, collector = ObservationLog(), FindingCollector()
    reply = await _agent(observations, collector)._report(_args(["act-42"]))
    finding = collector.items[0]
    assert finding.confidence == "low"
    assert finding.evidence_ids == []
    assert "ungrounded" in reply and "act-42" in reply
