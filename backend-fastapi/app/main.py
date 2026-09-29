"""HTTP API. Start a scan, poll it, export it."""

import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import anthropic
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .artifacts import ArtifactStore, LocalArtifactStore, S3ArtifactStore
from .config import Settings, get_settings
from .models import Scan, ScanAccepted, ScanRequest, utcnow
from .report import render_markdown
from .scanner.runner import ScanRunner
from .security import RateLimiter, TargetGuard, TargetNotAllowedError
from .store import DynamoScanStore, MemoryScanStore, ScanStore

log = logging.getLogger("app")
_SCAN_ID = re.compile(r"^[0-9a-f]{32}$")


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
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
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

        if not runner.has_capacity():
            raise HTTPException(
                status_code=429,
                detail="The tester is busy with other scans. Try again in a minute.",
                headers={"Retry-After": "60"},
            )
        wait = limiter.check(_client_id(request))
        if wait is not None:
            minutes = max(1, round(wait / 60))
            raise HTTPException(
                status_code=429,
                detail=f"Scan limit reached for this address. Try again in about {minutes} min.",
                headers={"Retry-After": str(int(wait))},
            )

        scan = Scan(
            id=uuid.uuid4().hex,
            target_url=target,
            options=body.options,
            model=settings.anthropic_model,
        )
        await store.put(scan)
        runner.start(scan)
        return ScanAccepted(id=scan.id, status=scan.status)

    async def load(scan_id: str) -> Scan:
        if not _SCAN_ID.match(scan_id):
            raise HTTPException(status_code=404, detail="Scan not found.")
        scan = await store.get(scan_id)
        if scan is None:
            raise HTTPException(status_code=404, detail="Scan not found.")

        # A scan left "running" long past its limit belonged to a task that died.
        stale_after = timedelta(seconds=settings.scan_timeout_seconds + 180)
        if scan.status in ("queued", "running") and utcnow() - scan.updated_at > stale_after:
            scan.status, scan.error = "error", "The scan was interrupted before it finished."

        for finding in scan.findings:
            if finding.screenshot_key:
                finding.screenshot_url = await artifacts.url_for(finding.screenshot_key)
        for step in scan.timeline:
            if step.screenshot_key:
                step.screenshot_url = await artifacts.url_for(step.screenshot_key)
            if step.before_key:
                step.before_url = await artifacts.url_for(step.before_key)
        return scan

    @app.get("/scans/{scan_id}", response_model=Scan, tags=["scans"])
    async def get_scan(scan_id: str, response: Response) -> Scan:
        response.headers["Cache-Control"] = "no-store"
        return await load(scan_id)

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
