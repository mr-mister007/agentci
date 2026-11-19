from __future__ import annotations

import asyncio
from typing import Any

from agentci_core.config import AgentConfig, SimulationConfig
from agentci_core.models import TestCase, TestKind
from agentci_runner import AgentClient, MockRegistry, run_suite


def test_mocks_are_deterministic() -> None:
    mocks = MockRegistry(config=SimulationConfig(seed=7))
    assert mocks.seed_for("abc") == mocks.seed_for("abc")
    assert mocks.tokens_for("abc") == mocks.tokens_for("abc")
    assert mocks.tool_response("search", {"q": "x"})["mocked"] is True


def test_transport_capture_and_order() -> None:
    cases = [
        TestCase(id=f"t{i}", name=f"case {i}", kind=TestKind.HAPPY, input={"value": str(i)})
        for i in range(5)
    ]

    async def transport(request: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {"output": {"answer": request["input"]["value"]}}

    async def main() -> None:
        async with AgentClient(
            config=AgentConfig(endpoint="http://unused/invoke"),
            mocks=MockRegistry(config=SimulationConfig(seed=1)),
            transport=transport,
        ) as client:
            traces = await run_suite(cases, client, concurrency=3)
        assert [trace.test_id for trace in traces] == [case.id for case in cases]
        assert all(trace.status == "ok" for trace in traces)
        assert all(trace.total_tokens > 0 and trace.total_cost_usd > 0 for trace in traces)

    asyncio.run(main())


def test_agent_error_becomes_trace() -> None:
    async def transport(_: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("agent down")

    async def main() -> None:
        case = TestCase(name="case", input={"value": "x"})
        async with AgentClient(transport=transport) as client:
            trace = await client.invoke(case)
        assert trace.status == "error"
        assert "agent down" in (trace.error or "")

    asyncio.run(main())
