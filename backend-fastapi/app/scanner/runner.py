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
from ..models import SEVERITY_ORDER, Finding, ProgressEvent, Scan, ScanOptions, utcnow
from ..security import TargetGuard, TargetNotAllowedError
from ..store import ScanStore
from .agent import ExplorationAgent, brief_automated
from .browser import BrowserSession, page_key
from .findings import FindingCollector, build_automated_findings
from .observations import ObservationLog
from .reviewer import review_findings
from .usage import UsageTracker

log = logging.getLogger(__name__)


class ScanFailedError(RuntimeError):
    pass


class Reporter:
    """Appends progress and persists the scan so the frontend can poll it."""

    def __init__(self, scan: Scan, store: ScanStore) -> None:
        self.scan = scan
        self.store = store
        self._lock = asyncio.Lock()

    async def save(self) -> None:
        async with self._lock:
            self.scan.updated_at = utcnow()
            await self.store.put(self.scan)

    async def progress(self, message: str, kind: str = "info") -> None:
        self.scan.progress.append(ProgressEvent(at=utcnow(), message=message, kind=kind))
        log.info("scan %s: %s", self.scan.id, message)
        await self.save()


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
        self._tasks: set[asyncio.Task] = set()

    def has_capacity(self) -> bool:
        return self._active < self.settings.max_concurrent_scans

    def start(self, scan: Scan) -> None:
        self._active += 1
        task = asyncio.create_task(self._run(scan), name=f"scan-{scan.id}")
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self._active -= 1
        self._tasks.discard(task)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run(self, scan: Scan) -> None:
        settings = self.settings
        reporter = Reporter(scan, self.store)
        collector = FindingCollector()
        usage = UsageTracker(scan.model)
        scan.status = "running"
        scan.started_at = utcnow()
        scan.expires_at = int((utcnow() + timedelta(days=settings.scan_ttl_days)).timestamp())

        try:
            await asyncio.wait_for(
                self._execute(scan, reporter, collector, usage),
                timeout=settings.scan_timeout_seconds + 60,
            )
            scan.status = "done"
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
        deadline = time.monotonic() + settings.scan_timeout_seconds
        guard = TargetGuard(settings.allow_private_targets)
        observations = ObservationLog()

        await reporter.progress("Checking the address is safe to test")
        target = await guard.check_url(scan.target_url)

        session = BrowserSession(
            guard,
            observations,
            accessibility=options.check_accessibility,
            axe_path=settings.axe_path,
        )
        async with session:
            if options.check_accessibility and not session.accessibility_available:
                scan.notes.append("Accessibility checks were unavailable for this scan.")

            await reporter.progress(f"Opening {target}")
            try:
                status = await session.open(target)
            except PlaywrightError as exc:
                raise ScanFailedError(
                    f"The page could not be loaded: {str(exc).splitlines()[0][:200]}"
                ) from exc
            start_url = session.page.url
            facts = observations.of_kind("page-facts")
            load_ms = (facts[0].data.get("timing") or {}).get("load") if facts else None
            loaded = f"Page loaded in {load_ms / 1000:.1f}s" if load_ms else "Page loaded"
            if status and status >= 400:
                await reporter.progress(f"The page answered with HTTP {status}", "warning")
            else:
                await reporter.progress(loaded)

            # Deterministic evidence first: it is cheap, fast, and cannot hallucinate.
            visited = {page_key(u) for u in session.visited}
            others = [u for u in session.links if page_key(u) not in visited]
            for url in others[: options.max_pages - 1]:
                if time.monotonic() > deadline - 90:
                    break
                await reporter.progress(f"Checking {page_key(url)}")
                try:
                    await session.open(url)
                except PlaywrightError as exc:
                    observations.add("request-failed", url, f"{url} failed to load: {exc}", url=url)

            if session.links:
                await reporter.progress("Following links to find dead ends")
                await session.check_links(settings.max_link_checks)

            build_automated_findings(observations, collector, options)
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
                progress=reporter.progress,
                scan_id=scan.id,
                deadline=deadline,
            )
            try:
                await session.open(start_url)
                await reporter.progress("Handing the browser to Claude to try the main flows")
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
