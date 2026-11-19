from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from agentci_api import worker as worker_module
from agentci_api.artifacts import get_artifact_store
from agentci_api.auth import set_repository_for_tests
from agentci_api.db import Database, set_database_for_tests
from agentci_api.queue import InMemoryQueue, RunJob, get_queue, set_queue_for_tests
from agentci_api.repository import ReportRepository
from agentci_core.models import Trace


class FakeClient:
    on_call = None

    def __init__(self) -> None:
        self.calls: list[str] = []

    @classmethod
    def from_config(cls, *_: object, **__: object) -> FakeClient:
        return cls()

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


async def _fake_run_suite(cases: list[object], client: FakeClient, concurrency: int) -> list[Trace]:
    assert concurrency >= 1
    trace = Trace(test_id=cases[0].id, output={"answer": "ok"})  # type: ignore[attr-defined]
    client.calls.append(trace.test_id)
    return [trace]


@pytest.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    await db.init()
    repository = ReportRepository(db)
    set_database_for_tests(db)
    set_repository_for_tests(repository)
    set_queue_for_tests(InMemoryQueue())
    get_artifact_store.cache_clear()
    try:
        yield db
    finally:
        set_queue_for_tests(None)
        set_repository_for_tests(None)
        set_database_for_tests(None)
        get_artifact_store.cache_clear()
        await db.close()


async def test_worker_processes_queued_job(
    database: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTCI_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_SHA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(worker_module, "AgentClient", FakeClient)
    monkeypatch.setattr(worker_module, "run_suite", _fake_run_suite)

    org = await worker_module.get_repository().create_org("Acme", "acme")
    job = RunJob(
        org_id=org.id,
        config_yaml="agent:\n  name: fake\nscorers: []\ngates: []\n",
        cases=[{"name": "queued", "input": {"q": "hi"}}],
        pr="12",
    )
    queue = get_queue()
    await queue.enqueue(job)

    result = await worker_module.run_evaluation(None, job.job_id)

    assert result["pass_rate"] == 1.0
    stored = await queue.get(job.job_id)
    assert stored is not None
    assert stored.status == "completed"
    report = await worker_module.get_repository().get_report(org.id, str(result["run_id"]))
    assert report is not None
    assert report.summary.total == 1
    assert len(report.traces) == 1
    assert await database.health() is None
