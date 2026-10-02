from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.artifacts import LocalArtifactStore
from app.config import Settings
from app.main import create_app
from app.models import Finding, Scan
from app.report import render_markdown
from app.store import MemoryScanStore


class FakeRunner:
    def __init__(self, capacity: bool = True) -> None:
        self.capacity = capacity
        self.started: list[Scan] = []

    thorough_capacity = True

    def has_capacity(self, depth: str = "quick") -> bool:
        return self.capacity and (depth != "thorough" or self.thorough_capacity)

    def start(self, scan: Scan) -> None:
        self.started.append(scan)

    def stop(self, scan_id: str) -> bool:
        return any(s.id == scan_id for s in self.started)

    async def shutdown(self) -> None:
        return None


@pytest.fixture
def parts(tmp_path: Path):
    settings = Settings(_env_file=None, rate_limit_scans=2, anthropic_api_key="test")
    store = MemoryScanStore()
    runner = FakeRunner()
    app = create_app(
        settings,
        store=store,
        runner=runner,
        artifacts=LocalArtifactStore(str(tmp_path), "http://testserver"),
    )
    return TestClient(app), store, runner


def test_health(parts) -> None:
    client, _, _ = parts
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["model"] == "claude-opus-5-5"


def test_start_and_poll_scan(parts) -> None:
    client, _, runner = parts
    response = client.post("/scans", json={"url": "1.1.1.1", "options": {"maxPages": 3}})
    assert response.status_code == 202
    scan_id = response.json()["id"]
    assert runner.started[0].options.max_pages == 3

    scan = client.get(f"/scans/{scan_id}").json()
    assert scan["targetUrl"] == "https://1.1.1.1/"
    assert scan["status"] == "queued"
    assert client.get(f"/scans/{scan_id}/report.md").status_code == 409


def test_private_targets_are_refused(parts) -> None:
    client, _, runner = parts
    response = client.post("/scans", json={"url": "http://169.254.169.254/latest/meta-data"})
    assert response.status_code == 422
    assert not runner.started


def test_rate_limit_per_client(parts) -> None:
    client, _, _ = parts
    headers = {"cf-connecting-ip": "203.0.113.7"}
    codes = [
        client.post("/scans", json={"url": "1.1.1.1"}, headers=headers).status_code
        for _ in range(3)
    ]
    assert codes == [202, 202, 429]
    other = client.post("/scans", json={"url": "1.1.1.1"}, headers={"cf-connecting-ip": "1.2.3.4"})
    assert other.status_code == 202


def test_only_the_owner_can_stop_a_running_scan(parts) -> None:
    client, store, _ = parts
    started = client.post("/scans", json={"url": "1.1.1.1"}).json()
    path = f"/scans/{started['id']}/stop"
    assert client.post(path, headers={"X-Owner-Token": "wrong"}).status_code == 403
    assert client.post(path, headers={"X-Owner-Token": started["ownerToken"]}).status_code == 202


def test_a_finished_scan_cannot_be_stopped(parts) -> None:
    client, store, _ = parts
    started = client.post("/scans", json={"url": "1.1.1.1"}).json()
    scan = store._scans[started["id"]]
    store._scans[started["id"]] = scan.replace('"status":"queued"', '"status":"done"')
    stop = client.post(
        f"/scans/{started['id']}/stop", headers={"X-Owner-Token": started["ownerToken"]}
    )
    assert stop.status_code == 409


def test_busy_runner_returns_429(parts) -> None:
    client, _, runner = parts
    runner.capacity = False
    assert client.post("/scans", json={"url": "1.1.1.1"}).status_code == 429


def test_a_second_thorough_scan_is_pointed_to_a_quick_one(parts) -> None:
    client, _, runner = parts
    runner.thorough_capacity = False
    thorough = client.post("/scans", json={"url": "1.1.1.1", "options": {"depth": "thorough"}})
    assert thorough.status_code == 429
    assert "Run a quick scan" in thorough.json()["detail"]
    quick = client.post("/scans", json={"url": "1.1.1.1", "options": {"depth": "quick"}})
    assert quick.status_code == 202
    assert client.get(f"/scans/{quick.json()['id']}").json()["options"]["depth"] == "quick"


def test_scan_depth_is_validated(parts) -> None:
    client, _, _ = parts
    bad = client.post("/scans", json={"url": "1.1.1.1", "options": {"depth": "forever"}})
    assert bad.status_code == 422


def test_unknown_or_malformed_ids_are_404(parts) -> None:
    client, _, _ = parts
    assert client.get("/scans/not-a-scan").status_code == 404
    assert client.get("/scans/" + "0" * 32).status_code == 404


def test_cors_allows_only_configured_origins(parts) -> None:
    client, _, _ = parts
    allowed = client.options(
        "/scans",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    blocked = client.options(
        "/scans",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in blocked.headers


async def test_finished_scan_exports_markdown(parts) -> None:
    client, store, _ = parts
    scan = Scan(
        id="a" * 32,
        target_url="https://app.example/",
        status="done",
        options={},
        model="claude-opus-5-5",
        summary="Sign-up is broken.",
    )
    scan.findings.append(
        Finding(
            id="f-1",
            title="Sign-up returns 500",
            category="bug",
            severity="high",
            confidence="high",
            kind="functional",
            source="agent",
            location="/signup",
            evidence="POST /api/signup 500",
            steps=["Open /signup", "Submit"],
            suggestion="Handle the empty password.",
            evidence_ids=["act-2"],
        )
    )
    await store.put(scan)

    report = client.get(f"/scans/{scan.id}/report.md")
    assert report.status_code == 200
    assert report.headers["x-content-type-options"] == "nosniff"
    assert "## Broken" in report.text
    assert "1. Open /signup" in report.text
    assert render_markdown(scan).startswith("# AI App Tester report")


def test_page_text_cannot_close_the_evidence_block() -> None:
    finding = Finding(
        id="f-1",
        title="Console error",
        category="bug",
        severity="medium",
        confidence="high",
        kind="console",
        source="automated",
        location="/",
        evidence="Logged: ```\n## Fake heading from the page\n```",
        steps=["Open /"],
        suggestion="Fix it.",
    )
    scan = Scan(
        id="b" * 32, target_url="https://app.example/", status="done", options={}, model="m"
    )
    scan.findings.append(finding)
    report = render_markdown(scan)
    # The block opens with a longer fence than any run inside it, so the page's
    # own ``` lines stay text instead of ending the block.
    assert "````text\nLogged: ```\n## Fake heading from the page\n```\n````" in report
