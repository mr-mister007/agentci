from __future__ import annotations

import asyncio
from pathlib import Path

from agentci_api.auth import set_repository_for_tests
from agentci_api.db import Database, set_database_for_tests
from agentci_api.queue import InMemoryQueue, set_queue_for_tests
from agentci_api.repository import ReportRepository
from agentci_core.models import Report, RunSummary
from fastapi.testclient import TestClient


def _report(passed: bool, pr: str = "12") -> Report:
    return Report(
        summary=RunSummary(
            agent="demo",
            pr=pr,
            total=2,
            passed=2 if passed else 1,
            pass_rate=1.0 if passed else 0.5,
        ),
        gate_passed=passed,
        auto_approve=passed,
    )


async def _seed(tmp_path: Path) -> tuple[Database, str, Report]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    await database.init()
    repository = ReportRepository(database)
    set_database_for_tests(database)
    set_repository_for_tests(repository)
    org = await repository.create_org("Acme", "acme")
    key, _ = await repository.create_api_key(org.id, "ci")
    report = _report(True)
    await repository.save_report(report, org.id)
    return database, key, report


def test_api_endpoints(tmp_path: Path) -> None:
    import agentci_api.main as api_main

    database, key, report = asyncio.run(_seed(tmp_path))
    api_main.settings.bootstrap_api_key = key
    set_queue_for_tests(InMemoryQueue())
    headers = {"X-API-Key": key}
    try:
        client = TestClient(api_main.app)
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/runs").status_code == 401
        listed = client.get("/runs", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["run_id"] == report.summary.run_id
        assert client.get(f"/runs/{report.summary.run_id}", headers=headers).status_code == 200
        assert client.get("/pr/12/status", headers=headers).json()["state"] == "success"
        assert client.get("/runs/missing", headers=headers).status_code == 404

        second = _report(False, pr="13")
        created = client.post("/runs", json=second.model_dump(mode="json"), headers=headers)
        assert created.status_code == 201
        assert created.json()["status"] == "stored"

        queued = client.post(
            "/runs/async",
            json={
                "config_yaml": "agent:\n  name: demo\n",
                "cases": [{"name": "queued", "input": {"q": "hi"}}],
                "pr": "12",
            },
            headers=headers,
        )
        assert queued.status_code == 202
        job = client.get(f"/jobs/{queued.json()['job_id']}", headers=headers)
        assert job.status_code == 200
        assert job.json()["status"] == "queued"

        webhook = client.post("/webhooks/github", json={"number": 12})
        assert webhook.json()["state"] == "success"
    finally:
        set_queue_for_tests(None)
        set_repository_for_tests(None)
        set_database_for_tests(None)
        asyncio.run(database.close())
