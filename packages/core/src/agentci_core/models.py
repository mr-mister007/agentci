from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TestKind(str, Enum):
    HAPPY = "happy"
    EDGE = "edge"
    ADVERSARIAL = "adversarial"


class SpanKind(str, Enum):
    LLM = "llm"
    TOOL = "tool"
    API = "api"
    DB = "db"


class TestCase(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    kind: TestKind = TestKind.HAPPY
    input: dict[str, Any]
    expected: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source: str = "manual"


class Span(BaseModel):
    span_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: SpanKind
    name: str
    start_ms: float = 0.0
    duration_ms: float = 0.0
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None


class Trace(BaseModel):
    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    test_id: str
    spans: list[Span] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = 0.0
    output: dict[str, Any] = Field(default_factory=dict)
    status: Literal["ok", "error"] = "ok"
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens_in + s.tokens_out for s in self.spans)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.spans)


class MetricResult(BaseModel):
    name: str
    value: float
    threshold: float | None = None
    direction: Literal["gte", "lte"] = "gte"
    passed: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


class EvalResult(BaseModel):
    test_id: str
    test_name: str
    kind: TestKind
    passed: bool
    metrics: list[MetricResult] = Field(default_factory=list)
    trace_id: str | None = None
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    failure_reason: str | None = None


class RunSummary(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    agent: str
    pr: str | None = None
    commit: str | None = None
    branch: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    baseline_run_id: str | None = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    cost_delta_usd: float = 0.0
    tool_accuracy: float = 1.0
    safety_score: float = 1.0
    policy_score: float = 1.0
    regressions: int = 0
    results: list[EvalResult] = Field(default_factory=list)


class Report(BaseModel):
    summary: RunSummary
    gate_passed: bool
    gates: list[GateResult] = Field(default_factory=list)
    auto_approve: bool = False
    traces: list[Trace] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GateResult(BaseModel):
    metric: str
    actual: float
    threshold: float
    direction: Literal["gte", "lte"]
    passed: bool
    message: str


class GateDecision(BaseModel):
    passed: bool
    results: list[GateResult] = Field(default_factory=list)
    auto_approve: bool = False
    status: Literal["success", "failure", "neutral"] = "success"
    summary: str = ""
