"""History sidebar and delete: ownership, cleanup, and what is never exposed."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.artifacts import LocalArtifactStore
from app.config import Settings
from app.main import create_app
from app.models import Finding, TimelineStep, utcnow
from app.store import MemoryScanStore


class FakeRunner:
    def __init__(self) -> None:
        self.started = []

    def has_capacity(self) -> bool:
        return True

    def start(self, scan) -> None:  # noqa: ANN001
        self.started.append(scan)

    async def shutdown(self) -> None:
        return None


@pytest.fixture
def env(tmp_path: Path):
    store = MemoryScanStore()
    artifacts = LocalArtifactStore(str(tmp_path), "http://testserver")
    runner = FakeRunner()
    app = create_app(
        Settings(_env_file=None, anthropic_api_key="test"),
        store=store,
        runner=runner,
        artifacts=artifacts,
    )
    return TestClient(app), store, artifacts, runner, tmp_path


def _start(client: TestClient) -> tuple[str, str]:
    body = client.post("/scans", json={"url": "1.1.1.1"}).json()
    return body["id"], body["ownerToken"]


async def _finish(store: MemoryScanStore, artifacts: LocalArtifactStore, scan_id: str) -> None:
    scan = await store.get(scan_id)
    scan.status = "done"
    scan.finished_at = utcnow()
    await artifacts.save_jpeg(f"{scan_id}/steps/001.jpg", b"frame")
    await artifacts.save_jpeg(f"{scan_id}/f-1.jpg", b"evidence")
    scan.timeline.append(
        TimelineStep(
            index=1,
            at=utcnow(),
            kind="visit",
            label="Opened /",
            screenshot_key=f"{scan_id}/steps/001.jpg",
        )
    )
    scan.findings.append(
        Finding(
            id="f-1",
            title="Broken",
            category="bug",
            severity="high",
            confidence="high",
            kind="functional",
            source="agent",
            location="/",
            evidence="e",
            steps=["s"],
            suggestion="x",
        )
    )
    await store.put(scan)


def test_starting_a_scan_returns_an_owner_token_but_never_its_hash(env) -> None:
    client, *_ = env
    scan_id, token = _start(client)
    assert len(token) >= 24
    scan = client.get(f"/scans/{scan_id}").json()
    assert "ownerTokenHash" not in scan and "owner_token_hash" not in scan
    assert token not in str(scan)


async def test_history_returns_summaries_and_skips_unknown_ids(env) -> None:
    client, store, artifacts, *_ = env
    done_id, _ = _start(client)
    running_id, _ = _start(client)
    await _finish(store, artifacts, done_id)

    ids = ",".join([done_id, running_id, "f" * 32, "not-an-id", done_id])
    summaries = client.get("/scans", params={"ids": ids}).json()

    assert [s["id"] for s in summaries] == [done_id, running_id]
    done = summaries[0]
    assert done["status"] == "done" and done["bugs"] == 1 and done["improvements"] == 0
    assert done["thumbnailUrl"].endswith(f"{done_id}/steps/001.jpg")
    assert "ownerTokenHash" not in done


async def test_delete_removes_the_record_and_every_screenshot(env) -> None:
    client, store, artifacts, _, root = env
    scan_id, token = _start(client)
    await _finish(store, artifacts, scan_id)
    assert (root / scan_id).exists()

    response = client.delete(f"/scans/{scan_id}", headers={"X-Owner-Token": token})

    assert response.status_code == 204
    assert await store.get(scan_id) is None
    assert not (root / scan_id).exists()
    assert client.get(f"/scans/{scan_id}").status_code == 404


async def test_delete_requires_the_owner_token(env) -> None:
    client, store, artifacts, *_ = env
    scan_id, _ = _start(client)
    await _finish(store, artifacts, scan_id)
    other_id, other_token = _start(client)

    assert client.delete(f"/scans/{scan_id}").status_code == 403
    assert (
        client.delete(f"/scans/{scan_id}", headers={"X-Owner-Token": other_token}).status_code
        == 403
    )
    assert await store.get(scan_id) is not None


def test_a_running_scan_cannot_be_deleted(env) -> None:
    client, *_ = env
    scan_id, token = _start(client)
    response = client.delete(f"/scans/{scan_id}", headers={"X-Owner-Token": token})
    assert response.status_code == 409


def test_cors_permits_delete_with_the_owner_header(env) -> None:
    client, *_ = env
    preflight = client.options(
        "/scans/abc",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "x-owner-token",
        },
    )
    assert preflight.status_code == 200
    assert "DELETE" in preflight.headers["access-control-allow-methods"]


async def test_local_prefix_delete_cannot_escape_the_artifact_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    store = LocalArtifactStore(str(tmp_path / "artifacts"), "http://x")
    assert await store.delete_prefix("../") == 0
    assert outside.exists()
