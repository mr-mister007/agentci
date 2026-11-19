from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import yaml
from agentci_core.models import Report
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agentci_api.artifacts import get_artifact_store
from agentci_api.auth import OrgContext, get_repository, require_admin, require_org
from agentci_api.db import get_database
from agentci_api.integrations import post_github_status, verify_github_signature
from agentci_api.observability import (
    QUEUE_DEPTH,
    RequestIDMiddleware,
    configure_logging,
    get_logger,
    metrics_response,
    observe_run,
)
from agentci_api.queue import RunJob, enqueue_run, get_job, get_queue
from agentci_api.quotas import get_quota_service
from agentci_api.rate_limit import get_rate_limiter
from agentci_api.settings import get_settings

settings = get_settings()
configure_logging(settings.environment)
logger = get_logger("agentci.api")


async def _bootstrap() -> None:
    key = settings.bootstrap_api_key
    if not key:
        logger.warning("bootstrap_key_not_configured")
        return
    repository = get_repository()
    org = await repository.resolve_api_key(key)
    if org is None:
        slug = settings.bootstrap_org_name.strip().lower().replace(" ", "-") or "default"
        org = await repository.create_org(
            name=settings.bootstrap_org_name,
            slug=slug,
            monthly_budget_usd=settings.monthly_budget_usd,
            monthly_run_quota=settings.monthly_run_quota,
        )
    await repository.import_api_key(org.id, "bootstrap", key)
    logger.info("bootstrap_ready", org_id=org.id)


async def _refresh_queue_depth() -> None:
    try:
        QUEUE_DEPTH.set(await get_queue().depth())
    except Exception:  # noqa: BLE001 - metrics must never break a request
        logger.warning("queue_metrics_failed")


async def _queue_metrics_loop() -> None:
    while True:
        await _refresh_queue_depth()
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await get_database().init()
    await _bootstrap()
    if settings.environment in {"prod", "production"} and not os.environ.get(
        "AGENTCI_GITHUB_WEBHOOK_SECRET"
    ):
        logger.warning("github_webhook_secret_not_configured")
    metrics_task = asyncio.create_task(_queue_metrics_loop())
    try:
        yield
    finally:
        metrics_task.cancel()
        with suppress(asyncio.CancelledError):
            await metrics_task
        await get_database().close()


app = FastAPI(
    title="AgentCI API",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "prod" else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["X-API-Key", "X-Admin-Token", "X-Request-ID", "Content-Type", "Idempotency-Key"],
)
app.add_middleware(RequestIDMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_error",
        request_id=getattr(request.state, "request_id", None),
        error_type=type(exc).__name__,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


async def _rate_limit(request: Request, org: OrgContext) -> None:
    allowed, remaining = await get_rate_limiter().check_and_increment(
        f"{org.id}:{request.url.path}", limit=60
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )
    request.state.rate_remaining = remaining


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "agentci",
        "version": "0.2.0",
        "environment": settings.environment,
    }


@app.get("/ready")
async def ready() -> dict[str, str]:
    await get_database().health()
    if os.environ.get("AGENTCI_REDIS_URL"):
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
            os.environ["AGENTCI_REDIS_URL"]
        )
        try:
            await client.ping()
        finally:
            await client.aclose()
    return {"status": "ready"}


@app.get("/metrics")
def metrics() -> Any:
    return metrics_response()


@app.post("/orgs")
async def create_org(
    payload: dict[str, Any],
    _: None = Depends(require_admin),
) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    slug = str(payload.get("slug", "")).strip().lower()
    if not name or not slug:
        raise HTTPException(status_code=422, detail="name and slug are required")
    org = await get_repository().create_org(
        name,
        slug,
        monthly_budget_usd=float(payload.get("monthly_budget_usd", settings.monthly_budget_usd)),
        monthly_run_quota=int(payload.get("monthly_run_quota", settings.monthly_run_quota)),
    )
    key, prefix = await get_repository().create_api_key(org.id, "default")
    return {"org": org.model_dump(), "api_key": key, "prefix": prefix}


@app.post("/orgs/{org_id}/keys")
async def create_key(
    org_id: str,
    payload: dict[str, Any] | None = None,
    _: None = Depends(require_admin),
) -> dict[str, str]:
    org = await get_repository().get_org(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    name = str((payload or {}).get("name", "default"))
    key, prefix = await get_repository().create_api_key(org_id, name)
    return {"api_key": key, "prefix": prefix}


@app.get("/orgs/{org_id}/keys")
async def list_keys(
    org_id: str,
    _: None = Depends(require_admin),
) -> list[dict[str, Any]]:
    org = await get_repository().get_org(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return await get_repository().list_api_keys(org_id)


@app.delete("/orgs/{org_id}/keys/{key_id}")
async def revoke_key(
    org_id: str,
    key_id: str,
    _: None = Depends(require_admin),
) -> dict[str, str]:
    if not await get_repository().revoke_api_key(org_id, key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "revoked"}


@app.post("/runs", status_code=201)
async def create_run(
    report: Report,
    request: Request,
    org: OrgContext = Depends(require_org),
) -> dict[str, Any]:
    await _rate_limit(request, org)
    await get_quota_service().check(org, report.summary.total_cost_usd)
    await get_repository().save_report(report, org.id)

    artifacts = await _store_artifacts(report)
    await get_quota_service().record(org.id, report.summary.total_cost_usd)
    observe_run(report)
    check_url = post_github_status(report, os.environ.get("AGENTCI_REPORT_URL"))
    return {
        "run_id": report.summary.run_id,
        "status": "stored",
        "artifacts": artifacts,
        "github_check": check_url,
    }


async def _store_artifacts(report: Report) -> dict[str, str]:
    store = get_artifact_store()
    run_id = report.summary.run_id
    return {
        "report": await store.put(
            run_id,
            "report.json",
            report.model_dump_json(indent=2).encode(),
            "application/json",
        ),
        "traces": await store.put(
            run_id,
            "traces.jsonl",
            "".join(
                json.dumps(trace.model_dump(mode="json"), sort_keys=True) + "\n"
                for trace in report.traces
            ).encode(),
            "application/x-ndjson",
        ),
    }


@app.post("/runs/async", status_code=202)
async def create_async_run(
    payload: dict[str, Any],
    request: Request,
    org: OrgContext = Depends(require_org),
) -> dict[str, str]:
    await _rate_limit(request, org)
    config_yaml = str(payload.get("config_yaml", ""))
    cases = payload.get("cases")
    if not config_yaml or not isinstance(cases, list):
        raise HTTPException(status_code=422, detail="config_yaml and cases are required")

    try:
        config = yaml.safe_load(config_yaml) or {}
        budget = float(config.get("budget_usd_per_run", 0.05))
    except (yaml.YAMLError, TypeError, ValueError):
        budget = 0.05
    await get_quota_service().check(org, budget * max(1, len(cases)))

    job = RunJob(
        org_id=org.id,
        agent=str(payload.get("agent", "agent")),
        pr=str(payload["pr"]) if payload.get("pr") is not None else None,
        commit_sha=str(payload["commit"]) if payload.get("commit") else None,
        branch=str(payload["branch"]) if payload.get("branch") else None,
        config_yaml=config_yaml,
        cases=cases,
        baseline_yaml=payload.get("baseline_yaml"),
    )
    job_id = await enqueue_run(job)
    await _refresh_queue_depth()
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
async def read_job(job_id: str, org: OrgContext = Depends(require_org)) -> dict[str, Any]:
    job = await get_job(job_id)
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump(mode="json", exclude={"config_yaml", "cases", "baseline_yaml"})


@app.get("/runs")
async def list_runs(
    pr: str | None = None,
    org: OrgContext = Depends(require_org),
) -> list[dict[str, Any]]:
    reports = await get_repository().list_reports(org.id, pr)
    return [
        {
            "run_id": report.summary.run_id,
            "agent": report.summary.agent,
            "pr": report.summary.pr,
            "pass_rate": report.summary.pass_rate,
            "gate_passed": report.gate_passed,
            "cost_usd": report.summary.total_cost_usd,
            "created_at": report.created_at.isoformat(),
        }
        for report in reports
    ]


@app.get("/runs/{run_id}")
async def get_run(run_id: str, org: OrgContext = Depends(require_org)) -> Report:
    report = await get_repository().get_report(org.id, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return report


@app.get("/runs/{run_id}/artifacts")
async def run_artifacts(run_id: str, org: OrgContext = Depends(require_org)) -> dict[str, Any]:
    report = await get_repository().get_report(org.id, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Run not found")
    store = get_artifact_store()
    return {name: await store.presign(run_id, name) for name in ("report.json", "traces.jsonl")}


@app.get("/runs/{run_id}/trace/{test_id}")
async def get_trace(
    run_id: str,
    test_id: str,
    org: OrgContext = Depends(require_org),
) -> dict[str, Any]:
    report = await get_repository().get_report(org.id, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Run not found")
    from agentci_runner.replay import replay_trace

    trace = next((item for item in report.traces if item.test_id == test_id), None)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return replay_trace(trace)


@app.get("/pr/{pr}/status")
async def pr_status(pr: str, org: OrgContext = Depends(require_org)) -> dict[str, Any]:
    report = await get_repository().latest_for_pr(org.id, pr)
    if report is None:
        raise HTTPException(status_code=404, detail="No run for this PR")
    return {
        "pr": pr,
        "state": "success" if report.gate_passed else "failure",
        "run_id": report.summary.run_id,
        "auto_approve": report.auto_approve,
        "summary": report.summary.model_dump(mode="json", exclude={"results"}),
    }


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    payload_bytes = await request.body()
    secret = os.environ.get("AGENTCI_GITHUB_WEBHOOK_SECRET")
    if secret and not verify_github_signature(payload_bytes, x_hub_signature_256, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(payload_bytes or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON payload") from exc
    pr = str(payload.get("number") or payload.get("pull_request", {}).get("number", ""))
    if x_github_event not in {"pull_request", "push", None} or not pr:
        return {"ignored": True, "reason": "not a pull request event"}

    key = settings.bootstrap_api_key
    org = await get_repository().resolve_api_key(key) if key else None
    if org is None:
        return {"ignored": True, "reason": "no default organization"}
    report = await get_repository().latest_for_pr(org.id, pr)
    return {
        "ignored": False,
        "pr": pr,
        "state": "success" if report and report.gate_passed else "failure",
        "run_id": report.summary.run_id if report else None,
    }


def run() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("agentci_api.main:app", host="0.0.0.0", port=port, reload=False)
