from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from agentci_core.config import SourceConfig, load_config
from agentci_core.gates import evaluate_gates
from agentci_core.models import Report, RunSummary, TestCase
from agentci_eval import evaluate, summarize
from agentci_runner import AgentClient, run_suite
from agentci_testgen import parse_cases
from arq.connections import RedisSettings

from agentci_api.artifacts import get_artifact_store
from agentci_api.auth import get_repository
from agentci_api.db import get_database
from agentci_api.integrations import post_github_status
from agentci_api.observability import AGENT_CALLS, get_logger, observe_run
from agentci_api.queue import RunJob, get_job, get_queue
from agentci_api.quotas import get_quota_service

logger = get_logger("agentci.worker")


def _baseline(raw: str | None) -> RunSummary | None:
    if not raw:
        return None
    payload = yaml.safe_load(raw)
    if isinstance(payload, dict) and "summary" in payload:
        payload = payload["summary"]
    return RunSummary.model_validate(payload)


async def _store_artifacts(report: Report) -> dict[str, str]:
    store = get_artifact_store()
    run_id = report.summary.run_id
    report_bytes = report.model_dump_json(indent=2).encode()
    traces_bytes = "".join(
        json.dumps(trace.model_dump(mode="json"), sort_keys=True) + "\n" for trace in report.traces
    ).encode()
    return {
        "report": await store.put(run_id, "report.json", report_bytes, "application/json"),
        "traces": await store.put(run_id, "traces.jsonl", traces_bytes, "application/x-ndjson"),
    }


async def process_job(job: RunJob) -> tuple[Report, dict[str, Any]]:
    queue = get_queue()
    job.status = "running"
    await queue.save(job)

    repository = get_repository()
    org = await repository.get_org(job.org_id)
    if org is None:
        raise ValueError(f"unknown organization {job.org_id}")

    with tempfile.TemporaryDirectory(prefix="agentci-run-") as directory:
        root = Path(directory)
        config_path = root / "agentci.yaml"
        config_path.write_text(job.config_yaml, encoding="utf-8")
        cases_path = root / "tests" / "cases.yaml"
        cases_path.parent.mkdir(parents=True, exist_ok=True)
        cases_path.write_text(yaml.safe_dump(job.cases, sort_keys=False), encoding="utf-8")

        cfg = load_config(config_path)
        cfg.sources = [SourceConfig(generator="yaml", path="tests/cases.yaml")]
        cases: list[TestCase] = parse_cases(cases_path.read_text(encoding="utf-8"))
        base = _baseline(job.baseline_yaml)

        def observe_call(status: str) -> None:
            AGENT_CALLS.labels(status=status).inc()

        client = AgentClient.from_config(cfg.agent, cfg.simulation)
        client.on_call = observe_call
        async with client:
            traces = await run_suite(cases, client, concurrency=cfg.simulation.concurrency)

        results = evaluate(cases, traces, cfg.scorers)
        summary = summarize(
            results,
            agent=job.agent or cfg.agent.name,
            baseline=base,
            pr=job.pr,
            commit=job.commit_sha,
            branch=job.branch,
        )
        decision = evaluate_gates(
            summary,
            cfg.gates,
            base,
            auto_approve=cfg.baseline.auto_approve,
        )
        report = Report(
            summary=summary,
            gate_passed=decision.passed,
            gates=decision.results,
            auto_approve=decision.auto_approve,
            traces=traces,
        )

    await repository.save_report(report, job.org_id)
    artifacts = await _store_artifacts(report)
    await get_quota_service().record(job.org_id, report.summary.total_cost_usd)
    observe_run(report)
    check_url = post_github_status(report, os.environ.get("AGENTCI_REPORT_URL"))
    result = report.model_dump(mode="json")
    result["artifacts"] = artifacts
    if check_url:
        result["github_check"] = check_url
    return report, result


async def run_evaluation(ctx: Any, job_id: str) -> dict[str, Any]:
    job = await get_job(job_id)
    if job is None:
        raise ValueError(f"unknown job {job_id}")
    try:
        report, artifact_result = await process_job(job)
    except Exception as exc:  # noqa: BLE001 - failures are job results, not worker crashes
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {str(exc)[:500]}"
        await get_queue().save(job)
        logger.error("job_failed", job_id=job_id, error=job.error)
        raise
    job.status = "completed"
    job.result = {
        "run_id": report.summary.run_id,
        "gate_passed": report.gate_passed,
        "pass_rate": report.summary.pass_rate,
        "cost_usd": report.summary.total_cost_usd,
        "regressions": report.summary.regressions,
        "artifacts": artifact_result.get("artifacts", {}),
    }
    if check := artifact_result.get("github_check"):
        job.result["github_check"] = check
    await get_queue().save(job)
    logger.info("job_completed", job_id=job_id, run_id=report.summary.run_id)
    return job.result


async def startup(_: Any) -> None:
    await get_database().init()


async def shutdown(_: Any) -> None:
    await get_database().close()


class WorkerSettings:
    functions = [run_evaluation]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(
        os.environ.get("AGENTCI_REDIS_URL", "redis://localhost:6379/0")
    )
    max_jobs = 16
    job_timeout = 15 * 60
    retry_jobs = 2


def run_worker() -> None:
    import subprocess
    import sys

    raise SystemExit(
        subprocess.call([sys.executable, "-m", "arq", "agentci_api.worker.WorkerSettings"])
    )
