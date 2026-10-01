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
