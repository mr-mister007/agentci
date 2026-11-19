from __future__ import annotations

import os
from typing import Any

import uvicorn
from agentci_core.models import Report
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from agentci_api.store import ReportStore

app = FastAPI(title="AgentCI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
store = ReportStore()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agentci"}


@app.post("/runs", status_code=201)
def create_run(report: Report) -> dict[str, str]:
    store.save(report)
    return {"run_id": report.summary.run_id, "status": "stored"}


@app.get("/runs")
def list_runs(pr: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "run_id": report.summary.run_id,
            "agent": report.summary.agent,
            "pr": report.summary.pr,
            "pass_rate": report.summary.pass_rate,
            "gate_passed": report.gate_passed,
            "created_at": report.created_at.isoformat(),
        }
        for report in store.list(pr)
    ]


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> Report:
    report = store.get(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return report


@app.get("/runs/{run_id}/trace/{test_id}")
def get_trace(run_id: str, test_id: str) -> dict[str, Any]:
    trace = store.trace(run_id, test_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


@app.get("/pr/{pr}/status")
def pr_status(pr: str) -> dict[str, Any]:
    report = store.latest_for_pr(pr)
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
def github_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept a GitHub webhook and return the check status for the push."""
    pr = str(payload.get("number") or payload.get("pull_request", {}).get("number", ""))
    if not pr:
        return {"ignored": True, "reason": "not a pull request event"}
    report = store.latest_for_pr(pr)
    return {
        "ignored": False,
        "pr": pr,
        "state": "success" if report and report.gate_passed else "failure",
        "run_id": report.summary.run_id if report else None,
    }


def run() -> None:
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("agentci_api.main:app", host="0.0.0.0", port=port, reload=False)
