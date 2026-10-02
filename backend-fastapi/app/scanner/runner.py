"""Runs one scan end to end and keeps its record current for the polling UI.

Stages, each reported as progress:
1. Safety check on the address (SSRF guard)
2. Load the page and audit it
3. Crawl a few same-site pages and check links (deterministic evidence)
4. Claude explores the main flows through the browser
5. Claude reviews every finding against its evidence
6. Filter by the user's options, sort, save

Degrades rather than fails: if the Claude API is unreachable or rejects the
request, the scan still returns everything the browser checks found, with a
note saying the AI stages did not complete.
"""

import asyncio
import logging
import time
from datetime import timedelta

import anthropic
from playwright.async_api import Error as PlaywrightError

from ..artifacts import ArtifactStore
from ..config import Settings
from ..models import (
    SEVERITY_ORDER,
    Finding,
    ProgressEvent,
    Scan,
    ScanOptions,
    TimelineStep,
    utcnow,
)
from ..security import TargetGuard, TargetNotAllowedError
from ..store import ScanStore
from .agent import ExplorationAgent, brief_automated
from .browser import BrowserSession, page_key
from .findings import FindingCollector, build_automated_findings
from .observations import ObservationLog
from .playwright_export import attach_automated_tests
from .reviewer import review_findings
from .usage import UsageTracker

log = logging.getLogger(__name__)


# Share of the progress bar given to Claude's exploration, the longest stage.
EXPLORE_FROM = 0.25
EXPLORE_TO = 0.88


class ScanFailedError(RuntimeError):
    pass


# Stages map onto the three phases shown in the command log.
_PHASE_OF_STAGE = {
    "queued": "prepare",
    "checking": "prepare",
    "loading": "prepare",
    "crawling": "prepare",
    "exploring": "explore",
    "reviewing": "review",
    "done": "review",
    "error": "review",
}


class Reporter:
    """Records the run as it happens and persists it so the frontend can follow along.

    Every call becomes a timeline step in the command log. Steps can carry a
    screenshot of the page after the action and, for element actions, one from
    just before with the target's position, so the UI can replay the run.
    """

    def __init__(self, scan: Scan, store: ScanStore, artifacts: ArtifactStore) -> None:
        self.scan = scan
        self.store = store
        self.artifacts = artifacts
        self._lock = asyncio.Lock()

    async def save(self) -> None:
        async with self._lock:
            self.scan.updated_at = utcnow()
            await self.store.put(self.scan)

    async def _store_image(self, name: str, data: bytes | None) -> str | None:
        if data is None:
            return None
        key = f"{self.scan.id}/steps/{name}.jpg"
        try:
            await self.artifacts.save_jpeg(key, data)
        except Exception as exc:  # a missing frame should never fail the scan
            log.warning("could not store %s: %s", key, exc)
            return None
        return key

    async def step(
        self,
        kind: str,
        label: str,
        *,
        why: str | None = None,
        url: str | None = None,
        shot: bytes | None = None,
        before: bytes | None = None,
        box: dict[str, float] | None = None,
        status: str = "ok",
        action_id: str | None = None,
        finding_id: str | None = None,
        signals: int = 0,
        shot_key: str | None = None,
        code: str | None = None,
    ) -> None:
        index = len(self.scan.timeline) + 1
        self.scan.timeline.append(
            TimelineStep(
                index=index,
                at=utcnow(),
                kind=kind,
                label=label,
                phase=_PHASE_OF_STAGE.get(self.scan.stage, "prepare"),
                why=why,
                url=url,
                status=status,
                action_id=action_id,
                finding_id=finding_id,
                signals=signals,
                code=code,
                box=box,
                screenshot_key=shot_key or await self._store_image(f"{index:03d}", shot),
                before_key=await self._store_image(f"{index:03d}-before", before),
            )
        )
        progress_kind = {"finding": "finding", "warning": "warning"}.get(status, "info")
        if kind not in ("stage", "visit"):
            progress_kind = "finding" if kind == "finding" else "action"
        self.scan.progress.append(ProgressEvent(at=utcnow(), message=label, kind=progress_kind))
        log.info("scan %s: [%s] %s", self.scan.id, kind, label)
        await self.save()

    async def advance(self, stage: str, completion: float) -> None:
        """Move the progress bar. Never backwards, never past 1."""
        self.scan.stage = stage
        self.scan.completion = round(min(1.0, max(self.scan.completion, completion)), 3)
        await self.save()

    async def progress(self, message: str, kind: str = "info") -> None:
        status = {"warning": "warning", "finding": "finding"}.get(kind, "info")
        await self.step("stage", message, status=status)


def _apply_options(findings: list[Finding], options: ScanOptions) -> list[Finding]:
    selected = []
    for finding in findings:
        if finding.kind == "accessibility" and not options.check_accessibility:
            continue
        if finding.category == "bug" and not options.find_bugs:
            continue
        if finding.category == "improvement" and not options.find_improvements:
            continue
        selected.append(finding)
    return sorted(
        selected,
        key=lambda f: (SEVERITY_ORDER[f.severity], f.category != "bug", f.source != "automated"),
    )


class ScanRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        store: ScanStore,
        artifacts: ArtifactStore,
        client: anthropic.AsyncAnthropic,
    ) -> None:
        self.settings = settings
        self.store = store
        self.artifacts = artifacts
        self.client = client
        self._active = 0
        self._thorough = 0
        self._tasks: set[asyncio.Task] = set()

    def has_capacity(self, depth: str = "quick") -> bool:
        if self._active >= self.settings.max_concurrent_scans:
            return False
        return depth != "thorough" or self._thorough < self.settings.max_thorough_scans

    def start(self, scan: Scan) -> None:
        thorough = scan.options.depth == "thorough"
        self._active += 1
        self._thorough += thorough
        task = asyncio.create_task(self._run(scan), name=f"scan-{scan.id}")
        self._tasks.add(task)
        task.add_done_callback(lambda done: self._finished(done, thorough))

    def _finished(self, task: asyncio.Task, thorough: bool) -> None:
        self._active -= 1
        self._thorough -= thorough
        self._tasks.discard(task)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, scan: Scan) -> None:
        settings = self.settings
        reporter = Reporter(scan, self.store, self.artifacts)
        collector = FindingCollector()
        usage = UsageTracker(scan.model)
        scan.status = "running"
        scan.started_at = utcnow()
        scan.expires_at = int((utcnow() + timedelta(days=settings.scan_ttl_days)).timestamp())

        _, seconds = settings.budget(scan.options.depth)
        try:
            await asyncio.wait_for(
                self._execute(scan, reporter, collector, usage),
                timeout=seconds + 60,
            )
            scan.status = "done"
            scan.stage, scan.completion = "done", 1.0
        except TimeoutError:
            scan.notes.append("The scan hit its time limit, so these results are partial.")
            scan.findings = scan.findings or _apply_options(collector.items, scan.options)
            scan.status = "done"
        except (TargetNotAllowedError, ScanFailedError) as exc:
            scan.status, scan.error = "error", str(exc)
        except asyncio.CancelledError:
            scan.status, scan.error = "error", "The scan was interrupted by a server restart."
            raise
        except Exception:
            log.exception("scan %s failed", scan.id)
            scan.findings = scan.findings or _apply_options(collector.items, scan.options)
            scan.status = "error"
            scan.error = "The scan hit an unexpected problem. Any results found are shown."
        finally:
            if scan.status == "error":
                scan.stage = "error"
            elif scan.status == "done":
                scan.stage, scan.completion = "done", 1.0
            scan.usage = usage.usage
            scan.finished_at = utcnow()
            await reporter.save()

    async def _execute(
        self,
        scan: Scan,
        reporter: Reporter,
        collector: FindingCollector,
        usage: UsageTracker,
    ) -> None:
        settings, options = self.settings, scan.options
        steps, seconds = settings.budget(options.depth)
        deadline = time.monotonic() + seconds
        guard = TargetGuard(settings.allow_private_targets)
        observations = ObservationLog()

        scan.agent_budget = steps
        await reporter.advance("checking", 0.02)
        await reporter.progress("Checking the address is safe to test")
        target = await guard.check_url(scan.target_url)

        session = BrowserSession(
            guard,
            observations,
            accessibility=options.check_accessibility,
            axe_path=settings.axe_path,
        )
        # Whether axe-core really ran, so the reviewer can weigh accessibility claims against it.
        accessibility_checked = options.check_accessibility and session.accessibility_available
        async with session:
            if options.check_accessibility and not session.accessibility_available:
                scan.notes.append("Accessibility checks were unavailable for this scan.")

            await reporter.advance("loading", 0.05)
            await reporter.progress(f"Opening {target}")
            try:
                status = await session.open(target)
            except PlaywrightError as exc:
                raise ScanFailedError(
                    f"The page could not be loaded: {str(exc).splitlines()[0][:400]}"
                ) from exc
            start_url = session.page.url
            facts = observations.of_kind("page-facts")
            load_ms = (facts[0].data.get("timing") or {}).get("load") if facts else None
            loaded = f"Page loaded in {load_ms / 1000:.1f}s" if load_ms else "Page loaded"
            failed_status = bool(status and status >= 400)
            await reporter.step(
                "visit",
                f"The page answered with HTTP {status}" if failed_status else loaded,
                url=start_url,
                shot=await _safe_shot(session),
                status="warning" if failed_status else "ok",
            )

            # Deterministic evidence first: it is cheap, fast, and cannot hallucinate.
            visited = {page_key(u) for u in session.visited}
            others = [u for u in session.links if page_key(u) not in visited]
            crawl = others[: options.max_pages - 1]
            for position, url in enumerate(crawl, start=1):
                await reporter.advance("crawling", 0.08 + 0.08 * position / len(crawl))
                if time.monotonic() > deadline - 90:
                    break
                try:
                    await session.open(url)
                    await reporter.step(
                        "visit", f"Checked {page_key(url)}", url=url, shot=await _safe_shot(session)
                    )
                except PlaywrightError as exc:
                    observations.add("request-failed", url, f"{url} failed to load: {exc}", url=url)
                    await reporter.step(
                        "visit", f"{page_key(url)} failed to load", url=url, status="failed"
                    )

            if session.links:
                await reporter.advance("crawling", 0.16)
                await reporter.progress("Following links to find dead ends")
                await session.check_links(settings.max_link_checks)

            build_automated_findings(observations, collector, options)
            attach_automated_tests(collector.items, observations)
            count = len(collector.items)
            await reporter.progress(
                f"Browser checks found {count} issue{'s' if count != 1 else ''}",
                "finding" if count else "info",
            )

            # Claude explores from the page the user asked about.
            agent = ExplorationAgent(
                client=self.client,
                settings=settings,
                session=session,
                observations=observations,
                collector=collector,
                artifacts=self.artifacts,
                usage=usage,
                recorder=reporter,
                scan_id=scan.id,
                deadline=deadline,
                budget=steps,
            )
            try:
                await session.open(start_url)
                await reporter.step(
                    "stage",
                    "Handing the browser to Claude to try the main flows",
                    url=start_url,
                    shot=await _safe_shot(session),
                )
                await reporter.advance("exploring", EXPLORE_FROM)
                await agent.run(brief_automated(collector.items))
            except anthropic.APIError as exc:
                log.warning("agent stopped: %s", exc)
                scan.notes.append(f"AI exploration stopped early ({_api_reason(exc)}).")
            except PlaywrightError as exc:
                scan.notes.append(f"AI exploration stopped early: {str(exc).splitlines()[0]}")
            scan.agent_steps = agent.steps
            scan.notes.extend(agent.notes)
            scan.visited_urls = list(session.visited)

        findings = list(collector.items)
        suppressed, summary = [], None
        if findings:
            await reporter.advance("reviewing", EXPLORE_TO)
            await reporter.progress("Double-checking every finding against its evidence")
            try:
                findings, suppressed, summary = await review_findings(
                    client=self.client,
                    settings=settings,
                    findings=findings,
                    observations=observations,
                    usage=usage,
                    target_url=target,
                    visited=scan.visited_urls,
                    accessibility_checked=accessibility_checked,
                )
            except anthropic.APIError as exc:
                scan.notes.append(f"The review step did not run ({_api_reason(exc)}).")

        scan.findings = _apply_options(findings, options)
        scan.suppressed = suppressed
        scan.summary = summary or agent.summary
        if suppressed:
            await reporter.progress(
                f"Filtered out {len(suppressed)} finding(s) as duplicates or likely noise"
            )
        await reporter.progress("Report ready")
        await reporter.advance("done", 1.0)


async def _safe_shot(session: BrowserSession) -> bytes | None:
    try:
        return await session.screenshot()
    except PlaywrightError:
        return None


def _api_reason(exc: anthropic.APIError) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        return "the Claude API key was rejected"
    if isinstance(exc, anthropic.RateLimitError):
        return "Claude API rate limit reached"
    if isinstance(exc, anthropic.APIConnectionError):
        return "could not reach the Claude API"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Claude API returned {exc.status_code}"
    return type(exc).__name__
