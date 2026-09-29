"""The Cypress-style replay: steps, frames, and stable links."""

from types import SimpleNamespace

from app.artifacts import S3ArtifactStore
from app.config import Settings
from app.models import Scan
from app.scanner.agent import TOOLS, ExplorationAgent
from app.scanner.browser import ActionOutcome
from app.scanner.findings import FindingCollector
from app.scanner.observations import ObservationLog
from app.scanner.runner import Reporter
from app.scanner.usage import UsageTracker
from app.store import MemoryScanStore


class _Artifacts:
    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}

    async def save_jpeg(self, key: str, data: bytes) -> None:
        self.saved[key] = data

    async def url_for(self, key: str) -> str:
        return f"https://cdn.example/{key}"


def _scan() -> Scan:
    return Scan(id="s" * 32, target_url="https://app.example/", options={}, model="m")


async def test_reporter_stores_frames_and_persists_each_step() -> None:
    scan, store, artifacts = _scan(), MemoryScanStore(), _Artifacts()
    reporter = Reporter(scan, store, artifacts)

    await reporter.progress("Checking the address is safe to test")
    await reporter.step(
        "click",
        "“Sign up”",
        why="Checking the form rejects empty input",
        shot=b"after",
        before=b"before",
        box={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05},
        action_id="act-1",
        status="failed",
    )

    stored = await store.get(scan.id)
    assert [s.kind for s in stored.timeline] == ["stage", "click"]
    click = stored.timeline[1]
    assert click.index == 2 and click.status == "failed" and click.action_id == "act-1"
    assert click.why == "Checking the form rejects empty input"
    assert click.box.w == 0.3
    assert artifacts.saved[click.screenshot_key] == b"after"
    assert artifacts.saved[click.before_key] == b"before"
    # The flat progress log still gets a line, so older clients keep working.
    assert stored.progress[-1].message == "“Sign up”"


class _Session:
    page = SimpleNamespace(url="https://app.example/signup")

    async def target_box(self, ref: str) -> dict[str, float]:
        return {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}

    async def screenshot(self) -> bytes:
        return b"frame"

    async def click(self, ref: str, label: str) -> ActionOutcome:
        return ActionOutcome("act-3", f'clicked {ref} "{label}"', False, self.page.url, 2)


class _Recorder:
    def __init__(self) -> None:
        self.steps: list[tuple[str, str, dict]] = []

    async def step(self, kind: str, label: str, **fields: object) -> None:
        self.steps.append((kind, label, fields))


async def test_click_is_recorded_with_before_after_frames_and_the_reason() -> None:
    recorder = _Recorder()
    agent = ExplorationAgent(
        client=None,
        settings=Settings(_env_file=None),
        session=_Session(),
        observations=ObservationLog(),
        collector=FindingCollector(),
        artifacts=_Artifacts(),
        usage=UsageTracker("claude-opus-5-5"),
        recorder=recorder,
        scan_id="abc",
        deadline=0,
    )
    agent.labels["e4"] = "Create account"

    content, is_error, done = await agent._execute(
        "click", {"ref": "e4", "why": "Submitting with every field empty"}
    )

    assert not is_error and not done
    assert content.startswith("act-3:")
    kind, label, fields = recorder.steps[0]
    assert (kind, label) == ("click", "“Create account”")
    assert fields["why"] == "Submitting with every field empty"
    assert fields["before"] == b"frame" and fields["shot"] == b"frame"
    assert fields["box"] == {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}
    assert fields["signals"] == 2


def test_every_action_tool_requires_a_reason() -> None:
    actions = {
        "click",
        "fill",
        "select_option",
        "press_key",
        "navigate",
        "go_back",
        "take_screenshot",
    }
    for tool in TOOLS:
        if tool["name"] in actions:
            assert "why" in tool["input_schema"]["required"], tool["name"]
            assert tool["strict"] is True


async def test_presigned_links_are_reused_so_the_replay_does_not_flicker() -> None:
    store = S3ArtifactStore.__new__(S3ArtifactStore)
    store.bucket, store._links = "b", {}
    calls = []

    class _S3:
        def generate_presigned_url(self, *_args, **kwargs) -> str:
            calls.append(kwargs)
            return f"https://s3.example/{kwargs['Params']['Key']}?sig={len(calls)}"

    store._s3 = _S3()
    first = await store.url_for("k.jpg")
    second = await store.url_for("k.jpg")
    assert first == second and len(calls) == 1
