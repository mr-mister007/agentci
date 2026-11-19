from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import httpx
from agentci_core.models import Report


def verify_github_signature(payload: bytes, signature: str | None, secret: str | None) -> bool:
    if not secret:
        return False
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def post_github_status(
    report: Report,
    target_url: str | None = None,
    token: str | None = None,
    repository: str | None = None,
    sha: str | None = None,
) -> str | None:
    token = token or os.environ.get("GITHUB_TOKEN")
    repository = repository or os.environ.get("GITHUB_REPOSITORY")
    sha = sha or os.environ.get("GITHUB_SHA")
    if not (token and repository and sha):
        return None

    summary = report.summary
    payload: dict[str, Any] = {
        "state": "success" if report.gate_passed else "failure",
        "context": "AgentCI",
        "description": (
            f"{summary.passed}/{summary.total} passed · p95 {summary.p95_latency_ms:.0f}ms · "
            f"${summary.total_cost_usd:.4f} · {summary.regressions} regressions"
        )[:140],
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
