"""Quick and thorough scans: separate budgets, and thorough ones never crowd out quick ones."""

import asyncio

from app.config import Settings
from app.models import Scan, ScanOptions
from app.scanner.runner import ScanRunner
from app.store import MemoryScanStore


def test_each_depth_has_its_own_budget() -> None:
    settings = Settings(_env_file=None)
    assert settings.budget("quick") == (30, 240)
    assert settings.budget("thorough") == (100, 1800)


def _scan(depth: str) -> Scan:
    return Scan(
        id=depth * 4, target_url="https://a.example/", options=ScanOptions(depth=depth), model="m"
    )


async def test_stopping_a_scan_keeps_what_it_found() -> None:
    store = MemoryScanStore()
    runner = ScanRunner(settings=Settings(_env_file=None), store=store, artifacts=None, client=None)

    async def explores_forever(scan, reporter, collector, usage) -> None:  # noqa: ANN001
        collector.add(
            title="Login button does nothing",
            category="bug",
            severity="high",
            confidence="high",
            kind="functional",
            source="agent",
            location="/login",
            evidence="Clicked it twice.",
            steps=["Open /login"],
            suggestion="Wire it up.",
        )
        await asyncio.Event().wait()

    runner._execute = explores_forever  # type: ignore[method-assign]
    scan = _scan("quick")
    runner.start(scan)
    await asyncio.sleep(0.05)
    assert runner.stop(scan.id)
    # A second Stop (another tab, a double click) while the task is still unwinding
    # must not cancel it again: that would land inside the final save.
    assert runner.stop(scan.id)
    await asyncio.gather(*runner._tasks, return_exceptions=True)

    saved = await store.get(scan.id)
    assert saved.status == "done"
    assert [f.title for f in saved.findings] == ["Login button does nothing"]
    assert any("Stopped early" in note for note in saved.notes)
    assert any("review step did not run" in note for note in saved.notes)
    assert not runner.stop(scan.id)  # already finished


async def test_one_thorough_scan_at_a_time_leaves_room_for_a_quick_one() -> None:
    runner = ScanRunner(
        settings=Settings(_env_file=None), store=MemoryScanStore(), artifacts=None, client=None
    )
    release = asyncio.Event()

    async def held(scan: Scan) -> None:  # stands in for a real scan until released
        await release.wait()

    runner._run = held  # type: ignore[method-assign]
    runner.start(_scan("thorough"))
    assert not runner.has_capacity("thorough")
    assert runner.has_capacity("quick")

    runner.start(_scan("quick"))
    assert not runner.has_capacity("quick")  # both slots taken

    release.set()
    await asyncio.sleep(0)
    await asyncio.gather(*runner._tasks)
    await asyncio.sleep(0)
    assert runner.has_capacity("thorough") and runner.has_capacity("quick")
