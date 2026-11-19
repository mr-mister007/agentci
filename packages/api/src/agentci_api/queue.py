from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


class RunJob(BaseModel):
    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    org_id: str
    agent: str = "agent"
    pr: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    config_yaml: str
    cases: list[dict[str, Any]] = Field(default_factory=list)
    baseline_yaml: str | None = None
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QueueBackend(Protocol):
    async def enqueue(self, job: RunJob) -> str: ...

    async def get(self, job_id: str) -> RunJob | None: ...

    async def save(self, job: RunJob) -> None: ...

    async def depth(self) -> int: ...


class InMemoryQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, RunJob] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, job: RunJob) -> str:
        async with self._lock:
            self._jobs[job.job_id] = job
        return job.job_id

    async def get(self, job_id: str) -> RunJob | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def save(self, job: RunJob) -> None:
        async with self._lock:
            self._jobs[job.job_id] = job

    async def depth(self) -> int:
        async with self._lock:
            return sum(1 for job in self._jobs.values() if job.status == "queued")


class RedisQueue:
    def __init__(self, url: str, prefix: str = "agentci:job:") -> None:
        self.url = url
        self.prefix = prefix
        self._redis: Any = None
        self._arq: Any = None

    async def _client(self) -> Any:
        if self._redis is None:
            import redis.asyncio as redis_asyncio

            self._redis = redis_asyncio.from_url(  # type: ignore[no-untyped-call]
                self.url, decode_responses=True
            )
        return self._redis

    async def _arq_pool(self) -> Any:
        if self._arq is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._arq = await create_pool(RedisSettings.from_dsn(self.url))
        return self._arq

    async def enqueue(self, job: RunJob) -> str:
        await self.save(job)
        pool = await self._arq_pool()
        await pool.enqueue_job("run_evaluation", job.job_id)
        return job.job_id

    async def get(self, job_id: str) -> RunJob | None:
        client = await self._client()
        payload = await client.get(f"{self.prefix}{job_id}")
        return RunJob.model_validate_json(payload) if payload else None

    async def save(self, job: RunJob) -> None:
        client = await self._client()
        job.updated_at = datetime.now(UTC)
        await client.set(
            f"{self.prefix}{job.job_id}",
            job.model_dump_json(),
            ex=24 * 60 * 60,
        )

    async def depth(self) -> int:
        client = await self._client()
        return int(await client.zcard("arq:queue"))


_QUEUE: QueueBackend | None = None


def get_queue() -> QueueBackend:
    global _QUEUE
    if _QUEUE is None:
        url = os.environ.get("AGENTCI_REDIS_URL")
        _QUEUE = RedisQueue(url) if url else InMemoryQueue()
    return _QUEUE


def set_queue_for_tests(queue: QueueBackend | None) -> None:
    global _QUEUE
    _QUEUE = queue


async def enqueue_run(job: RunJob) -> str:
    return await get_queue().enqueue(job)


async def get_job(job_id: str) -> RunJob | None:
    return await get_queue().get(job_id)
