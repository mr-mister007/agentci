from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from agentci_core.config import AgentConfig
from agentci_core.models import Span, SpanKind, TestCase, Trace
from pydantic import BaseModel, ConfigDict

from agentci_runner.mocks import MockRegistry

Transport = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentClient(BaseModel):
    """Calls the agent under test and normalises its response into a Trace."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: AgentConfig = AgentConfig()
    mocks: MockRegistry = MockRegistry()

    transport: Transport | None = None
    _client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> AgentClient:
        if self.transport is None:
            headers = {"Content-Type": "application/json"}
            if self.config.auth_env:
                token = os.environ.get(self.config.auth_env)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            self._client = httpx.AsyncClient(
                base_url=self.config.endpoint.rsplit("/", 1)[0],
                timeout=self.config.timeout_seconds,
                headers=headers,
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def build_request(self, case: TestCase) -> dict[str, Any]:
        return {
            **self.config.request_template,
            "test_id": case.id,
            "input": case.input,
            "expected": case.expected,
            "seed": self.mocks.seed_for(case.id),
            "mocks": self.mocks.payload(),
        }

    async def invoke(self, case: TestCase) -> Trace:
        request = self.build_request(case)
        try:
            if self.transport is not None:
                response = await self.transport(request)
            else:
                assert self._client is not None, "AgentClient must be used as a context manager"
                http_response = await self._client.post(
                    "/" + self.config.endpoint.rsplit("/", 1)[-1], json=request
                )
                http_response.raise_for_status()
                response = http_response.json()
        except Exception as exc:  # noqa: BLE001 - agent failures are first-class results
            return Trace(
                test_id=case.id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=0.0,
            )

        if not isinstance(response, dict):
            response = {"output": response}

        spans = self._spans(case, response)
        duration = sum(span.duration_ms for span in spans)
        if not spans:
            duration = self.mocks.latency_ms(case.id)

        return Trace(
            test_id=case.id,
            spans=spans,
            duration_ms=round(duration, 2),
            output=response.get("output") or {},
            status="error" if response.get("error") else "ok",
            error=response.get("error"),
        )

    def _spans(self, case: TestCase, response: dict[str, Any]) -> list[Span]:
        raw_spans = response.get("spans")
        if isinstance(raw_spans, list) and raw_spans:
            return [Span.model_validate(item) for item in raw_spans if isinstance(item, dict)]

        spans: list[Span] = []
        tool_calls = response.get("tool_calls") or []
        for index, call in enumerate(tool_calls):
            name = call.get("name", f"tool_{index}") if isinstance(call, dict) else f"tool_{index}"
            spans.append(
                Span(
                    kind=SpanKind.TOOL,
                    name=name,
                    duration_ms=self.mocks.latency_ms(case.id, "tool"),
                    input=call if isinstance(call, dict) else {"call": call},
                    output=self.mocks.tool_response(name, call if isinstance(call, dict) else {}),
                )
            )

        tokens_in, tokens_out = self.mocks.tokens_for(case.id)
        cost = response.get("cost_usd")
        spans.append(
            Span(
                kind=SpanKind.LLM,
                name=response.get("model", "agent-llm"),
                duration_ms=self.mocks.latency_ms(case.id, "llm"),
                input={"prompt": case.input},
                output=response.get("output") or {},
                tokens_in=int(response.get("tokens_in", tokens_in)),
                tokens_out=int(response.get("tokens_out", tokens_out)),
                cost_usd=float(cost) if cost is not None else round(tokens_out * 0.000015, 6),
            )
        )
        return spans
