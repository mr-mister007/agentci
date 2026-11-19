from __future__ import annotations

import asyncio
from collections.abc import Callable

from agentci_core.models import TestCase, Trace

from agentci_runner.client import AgentClient


async def run_trace(case: TestCase, client: AgentClient) -> Trace:
    return await client.invoke(case)


async def run_suite(
    cases: list[TestCase],
    client: AgentClient,
    concurrency: int = 8,
    on_complete: Callable[[Trace], None] | None = None,
) -> list[Trace]:
    """Run all cases concurrently while preserving input order."""
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def bounded(case: TestCase) -> Trace:
        async with semaphore:
            trace = await run_trace(case, client)
        if on_complete is not None:
            on_complete(trace)
        return trace

    return list(await asyncio.gather(*(bounded(case) for case in cases)))
