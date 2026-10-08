"""HTTP API. Start a scan, poll it, export it."""

import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import anthropic
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .artifacts import ArtifactStore, LocalArtifactStore, S3ArtifactStore
from .config import Settings, get_settings
from .models import Scan, ScanAccepted, ScanRequest, ScanSummary, utcnow
from .report import render_markdown
from .scanner.playwright_export import render_suite
from .scanner.runner import ScanRunner
from .security import RateLimiter, TargetGuard, TargetNotAllowedError
from .store import DynamoScanStore, MemoryScanStore, ScanStore

log = logging.getLogger("app")
_SCAN_ID = re.compile(r"^[0-9a-f]{32}$")
# History lookups are capped so one request cannot fan out into hundreds of reads.
MAX_HISTORY = 30


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _client_id(request: Request) -> str:
    # The ALB only accepts connections from Cloudflare's ranges (see backend-infra),
    # so this header is set by Cloudflare and cannot be forged by the caller.
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


def create_app(
    settings: Settings | None = None,
    *,
    store: ScanStore | None = None,
    artifacts: ArtifactStore | None = None,
    runner: ScanRunner | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if store is None:
        store = (
            DynamoScanStore(settings.dynamodb_table, settings.aws_region)
            if settings.dynamodb_table
            else MemoryScanStore()
        )
    if artifacts is None:
        artifacts = (
            S3ArtifactStore(settings.artifact_bucket, settings.aws_region)
            if settings.artifact_bucket
            else LocalArtifactStore(settings.local_artifact_dir, settings.public_base_url)
        )
    if runner is None:
        workspace = settings.anthropic_workspace_id
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,
            default_headers={"anthropic-workspace-id": workspace} if workspace else None,
        )
        runner = ScanRunner(settings=settings, store=store, artifacts=artifacts, client=client)
    limiter = RateLimiter(settings.rate_limit_scans, settings.rate_limit_window_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await runner.shutdown()

    app = FastAPI(
        title="AI App Tester API",
        version="0.1.0",
        description="Point it at a URL. Playwright gathers evidence, Claude explores and "
        "judges, and every finding cites what the browser actually recorded.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Owner-Token"],
        max_age=600,
    )

    @app.middleware("http")
    async def no_sniff(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        # Reports and test exports carry text from the tested site; a browser must
        # show them as the type they are declared as, never sniff them into HTML.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    if isinstance(artifacts, LocalArtifactStore):
        app.mount("/artifacts", StaticFiles(directory=artifacts.root), name="artifacts")

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict:
        return {
            "status": "ok",
            "model": settings.anthropic_model,
            "claudeConfigured": bool(
                settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
            ),
            "acceptingScans": runner.has_capacity(),
        }

    @app.post("/scans", status_code=202, response_model=ScanAccepted, tags=["scans"])
    async def start_scan(body: ScanRequest, request: Request) -> ScanAccepted:
        try:
            target = await TargetGuard(settings.allow_private_targets).check_url(body.url)
        except TargetNotAllowedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not runner.has_capacity(body.options.depth):
            busy = (
                "A thorough scan is already running. Run a quick scan, or try again later."
                if body.options.depth == "thorough" and runner.has_capacity()
                else "The tester is busy with other scans. Try again in a minute."
            )
            raise HTTPException(status_code=429, detail=busy, headers={"Retry-After": "60"})
        wait = limiter.check(_client_id(request))
        if wait is not None:
            minutes = max(1, round(wait / 60))
            raise HTTPException(
                status_code=429,
                detail=f"Scan limit reached for this address. Try again in about {minutes} min.",
                headers={"Retry-After": str(int(wait))},
            )

        owner_token = secrets.token_urlsafe(24)
        scan = Scan(
            id=uuid.uuid4().hex,
            target_url=target,
            options=body.options,
            model=settings.anthropic_model,
            owner_token_hash=_hash_token(owner_token),
        )
        await store.put(scan)
        runner.start(scan)
        return ScanAccepted(id=scan.id, status=scan.status, owner_token=owner_token)

    # A running scan saves after every step, and its longest quiet stretch (checking
    # links, one long Claude turn) is a few minutes, so this gap holds for quick and
    # thorough scans alike.
    stale_after = timedelta(seconds=settings.scan_timeout_seconds + 180)

    def settle_status(scan: Scan) -> Scan:
        # A scan that has stopped saving for that long belonged to a task that died.
        if scan.status in ("queued", "running") and utcnow() - scan.updated_at > stale_after:
            scan.status, scan.error = "error", "The scan was interrupted before it finished."
        return scan

    async def fetch(scan_id: str) -> Scan:
        if not _SCAN_ID.match(scan_id):
            raise HTTPException(status_code=404, detail="Scan not found.")
        scan = await store.get(scan_id)
        if scan is None:
            raise HTTPException(status_code=404, detail="Scan not found.")
        return settle_status(scan)

    async def load(scan_id: str) -> Scan:
        scan = await fetch(scan_id)

        for finding in scan.findings:
            if finding.screenshot_key:
                finding.screenshot_url = await artifacts.url_for(finding.screenshot_key)
        for step in scan.timeline:
            if step.screenshot_key:
                step.screenshot_url = await artifacts.url_for(step.screenshot_key)
            if step.before_key:
                step.before_url = await artifacts.url_for(step.before_key)
        return scan

    @app.get("/scans", response_model=list[ScanSummary], tags=["scans"])
    async def list_scans(
        response: Response,
        ids: str = Query(
            max_length=MAX_HISTORY * 33,
            description="Comma-separated scan ids from this browser's history.",
        ),
    ) -> list[ScanSummary]:
        """Summaries for the history sidebar.

        There are no accounts, so there is no "list everything" endpoint: the browser
        sends the ids it started, and ids that expired or were deleted are simply
        absent from the response, which tells the sidebar to forget them.
        """
        response.headers["Cache-Control"] = "no-store"
        wanted = list(dict.fromkeys(i for i in ids.split(",") if _SCAN_ID.match(i)))
        scans = await asyncio.gather(*(store.get(i) for i in wanted[:MAX_HISTORY]))
        summaries = []
        for scan in scans:
            if scan is None:
                continue
            settle_status(scan)
            thumb = next((s.screenshot_key for s in scan.timeline if s.screenshot_key), None)
            summaries.append(
                ScanSummary(
                    id=scan.id,
                    target_url=scan.target_url,
                    status=scan.status,
                    created_at=scan.created_at,
                    started_at=scan.started_at,
                    finished_at=scan.finished_at,
                    bugs=sum(1 for f in scan.findings if f.category == "bug"),
                    improvements=sum(1 for f in scan.findings if f.category == "improvement"),
                    thumbnail_url=await artifacts.url_for(thumb) if thumb else None,
                    error=scan.error,
                )
            )
        return summaries

    @app.post("/scans/{scan_id}/stop", status_code=202, tags=["scans"])
    async def stop_scan(
        scan_id: str,
        x_owner_token: str = Header(
            default="", description="Token returned when the scan started."
        ),
    ) -> dict:
        """Stop a running scan early. What it found so far is kept and reported."""
        scan = await fetch(scan_id)
        if not scan.owner_token_hash or not hmac.compare_digest(
            scan.owner_token_hash, _hash_token(x_owner_token)
        ):
            raise HTTPException(
                status_code=403, detail="Only the browser that started this scan can stop it."
            )
        if scan.status not in ("queued", "running"):
            raise HTTPException(status_code=409, detail="This scan has already finished.")
        if not runner.stop(scan.id):
            # Another server process owns the task (a deploy in progress), so the
            # scan carries on; the client must not pretend it stopped.
            raise HTTPException(
                status_code=503,
                detail="This scan cannot be stopped right now; it will finish on its own.",
            )
        return {"status": "stopping"}

    @app.delete("/scans/{scan_id}", status_code=204, tags=["scans"])
    async def delete_scan(
        scan_id: str,
        x_owner_token: str = Header(
            default="", description="Token returned when the scan started."
        ),
    ) -> Response:
        """Delete a scan's record and every screenshot it stored."""
        scan = await fetch(scan_id)
        if not scan.owner_token_hash or not hmac.compare_digest(
            scan.owner_token_hash, _hash_token(x_owner_token)
        ):
            raise HTTPException(
                status_code=403, detail="Only the browser that ran this scan can delete it."
            )
        if scan.status in ("queued", "running"):
            raise HTTPException(
                status_code=409, detail="This scan is still running. Delete it once it finishes."
            )
        try:
            # Screenshots first: if the record delete then failed, the scan would still
            # be listed and could be retried, rather than leaving orphaned files behind.
            removed = await artifacts.delete_prefix(f"{scan.id}/")
            await store.delete(scan.id)
        except (ClientError, BotoCoreError) as exc:
            log.error("delete of %s failed: %s", scan.id, exc)
            raise HTTPException(
                status_code=503, detail="The server could not delete this scan right now."
            ) from exc
        log.info("deleted scan %s and %d stored file(s)", scan.id, removed)
        return Response(status_code=204)

    @app.get(
        "/scans/{scan_id}",
        response_model=Scan,
        response_model_exclude={"owner_token_hash"},
        tags=["scans"],
    )
    async def get_scan(scan_id: str, response: Response) -> Scan:
        response.headers["Cache-Control"] = "no-store"
        return await load(scan_id)

    @app.get("/scans/{scan_id}/tests.spec.ts", response_class=PlainTextResponse, tags=["scans"])
    async def get_tests(scan_id: str) -> PlainTextResponse:
        """Every finding's Playwright regression test as one spec file."""
        scan = await fetch(scan_id)
        suite = render_suite(scan.findings)
        if suite is None:
            raise HTTPException(status_code=404, detail="This scan produced no tests.")
        return PlainTextResponse(
            suite,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="ai-app-tester-{scan.id[:8]}.spec.ts"'
            },
        )

    @app.get("/scans/{scan_id}/report.md", response_class=PlainTextResponse, tags=["scans"])
    async def get_report(scan_id: str) -> PlainTextResponse:
        scan = await load(scan_id)
        if scan.status not in ("done", "error"):
            raise HTTPException(status_code=409, detail="The scan has not finished yet.")
        return PlainTextResponse(
            render_markdown(scan),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="ai-app-tester-{scan.id[:8]}.md"'},
        )

    return app


app = create_app()
