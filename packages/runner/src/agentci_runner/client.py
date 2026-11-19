from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from agentci_core.config import AgentConfig, SimulationConfig
from agentci_core.models import Span, SpanKind, TestCase, Trace
from pydantic import BaseModel, ConfigDict, Field

from agentci_runner.mocks import MockRegistry
from agentci_runner.retry import RetryPolicy, with_retry
from agentci_runner.sandbox import (
    DockerSandbox,
    InProcessSandbox,
    SandboxHandle,
    SandboxSpec,
)
from agentci_runner.security import validate_endpoint

Transport = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
CallObserver = Callable[[str], None]
MAX_SPANS = 1000


class AgentClient(BaseModel):
    """Calls the agent under test and normalises its response into a Trace."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    config: AgentConfig = Field(default_factory=AgentConfig)
    mocks: MockRegistry = Field(default_factory=MockRegistry)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    sandbox_spec: SandboxSpec | None = None

    transport: Transport | None = None
    on_call: CallObserver | None = None
    _client: httpx.AsyncClient | None = None
    _handle: SandboxHandle | None = None
    _request_path: str = "/"

    @classmethod
    def from_config(
        cls,
        config: AgentConfig,
        simulation: SimulationConfig,
        mocks: MockRegistry | None = None,
    ) -> AgentClient:
        spec = SandboxSpec(
            sandbox=simulation.sandbox,
            image=simulation.image,
            runtime=simulation.runtime,
            network_mode=simulation.network_mode,
            memory_mb=simulation.memory_mb,
            cpu_quota=simulation.cpu_quota,
            pids_limit=simulation.pids_limit,
            read_only=simulation.read_only,
            timeout_seconds=config.timeout_seconds,
            env_allowlist=simulation.env_allowlist,
            allow_private_endpoint=simulation.allow_private_endpoint,
        )
        return cls(
            config=config,
            mocks=mocks or MockRegistry(config=simulation),
            retry=RetryPolicy.model_validate(simulation.retry or {}),
            sandbox_spec=spec,
        )

    async def __aenter__(self) -> AgentClient:
        parsed = urlsplit(self.config.endpoint)
        self._request_path = parsed.path or "/"
        if self.transport is not None:
            return self

        spec = self.sandbox_spec or SandboxSpec(timeout_seconds=self.config.timeout_seconds)
        if spec.sandbox == "inprocess":
            schemes = ("http", "https") if spec.allow_private_endpoint else ("https",)
            endpoint = validate_endpoint(
                self.config.endpoint,
                allow_private=spec.allow_private_endpoint,
                allowed_schemes=schemes,
            )
            self._handle = await InProcessSandbox(spec).start(endpoint)
        else:
            docker_sandbox = DockerSandbox(spec, health_path=self.config.health_path)
            self._handle = await docker_sandbox.start()

        assert self._handle is not None
        handle_url = urlsplit(self._handle.endpoint)
        origin = f"{handle_url.scheme}://{handle_url.netloc}"
        headers = {"Content-Type": "application/json"}
        if self.config.auth_env:
            token = os.environ.get(self.config.auth_env)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=origin,
            timeout=self.config.timeout_seconds,
            headers=headers,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._handle is not None:
            await self._handle.close()
            self._handle = None

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
                response = await with_retry(
                    lambda: self._post(request),
                    policy=self.retry,
                )
        except Exception as exc:  # noqa: BLE001 - agent failures are first-class results
            self._notify("error")
            return Trace(
                test_id=case.id,
                status="error",
                error=f"{type(exc).__name__}: {str(exc)[:500]}",
                duration_ms=0.0,
            )

        self._notify("ok")
        if not isinstance(response, dict):
            response = {"output": response}

        try:
            spans = self._spans(case, response)
        except Exception as exc:  # noqa: BLE001 - malformed agent responses are test failures
            return Trace(
                test_id=case.id,
                status="error",
                error=f"invalid agent response: {type(exc).__name__}: {str(exc)[:300]}",
            )

        duration = sum(span.duration_ms for span in spans)
        if not spans:
            duration = self.mocks.latency_ms(case.id)

        return Trace(
            test_id=case.id,
            spans=spans,
            duration_ms=round(duration, 2),
            output=response.get("output") or {},
            status="error" if response.get("error") else "ok",
            error=str(response.get("error"))[:500] if response.get("error") else None,
        )

    async def _post(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None, "AgentClient must be used as a context manager"
        http_response = await self._client.post(self._request_path, json=request)
        http_response.raise_for_status()
        payload = http_response.json()
        return payload if isinstance(payload, dict) else {"output": payload}

    def _notify(self, status: str) -> None:
        if self.on_call is not None:
            self.on_call(status)

    def _spans(self, case: TestCase, response: dict[str, Any]) -> list[Span]:
        raw_spans = response.get("spans")
        if isinstance(raw_spans, list) and raw_spans:
            return [
                Span.model_validate(item)
                for item in raw_spans[:MAX_SPANS]
                if isinstance(item, dict)
            ]

        spans: list[Span] = []
        tool_calls = response.get("tool_calls") or []
        for index, call in enumerate(tool_calls[:MAX_SPANS]):
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
                name=str(response.get("model", "agent-llm"))[:200],
                duration_ms=self.mocks.latency_ms(case.id, "llm"),
                input={"prompt": case.input},
                output=response.get("output") or {},
                tokens_in=int(response.get("tokens_in", tokens_in)),
                tokens_out=int(response.get("tokens_out", tokens_out)),
                cost_usd=float(cost) if cost is not None else round(tokens_out * 0.000015, 6),
            )
        )
        return spans
