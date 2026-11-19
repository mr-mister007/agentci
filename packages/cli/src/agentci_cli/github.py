from __future__ import annotations

import json
import os
from typing import Any

import httpx
from agentci_core.models import Report


def post_status(report: Report, target_url: str | None = None) -> str | None:
    """Post a commit status check to GitHub when running inside Actions."""
    token = os.environ.get("GITHUB_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY")
    sha = os.environ.get("GITHUB_SHA")
    if not (token and repository and sha):
        return None

    summary = report.summary
    state = "success" if report.gate_passed else "failure"
    description = (
        f"{summary.passed}/{summary.total} passed · "
        f"p95 {summary.p95_latency_ms:.0f}ms · "
        f"${summary.total_cost_usd:.4f} · {summary.regressions} regressions"
    )
    payload: dict[str, Any] = {
        "state": state,
        "context": "AgentCI",
        "description": description[:140],
    }
    if target_url:
        payload["target_url"] = target_url

    response = httpx.post(
        f"https://api.github.com/repos/{repository}/statuses/{sha}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    return f"https://github.com/{repository}/commit/{sha}/checks"


def push_report(report: Report, api_url: str | None = None) -> str | None:
    """Persist a report in the API when AGENTCI_API_URL is configured."""
    url = api_url or os.environ.get("AGENTCI_API_URL")
    if not url:
        return None
    response = httpx.post(
        url.rstrip("/") + "/runs",
        json=json.loads(report.model_dump_json()),
        timeout=15,
    )
    response.raise_for_status()
    return url
