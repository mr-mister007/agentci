"""AgentCI core package."""

from agentci_core.config import AgentConfig, GateConfig, LoadedConfig, load_config
from agentci_core.gates import GateDecision, GateResult, evaluate_gates
from agentci_core.models import (
    EvalResult,
    MetricResult,
    Report,
    RunSummary,
    Span,
    SpanKind,
    TestCase,
    TestKind,
    Trace,
)

__all__ = [
    "AgentConfig",
    "EvalResult",
    "GateConfig",
    "GateDecision",
    "GateResult",
    "LoadedConfig",
    "MetricResult",
    "Report",
    "RunSummary",
    "Span",
    "SpanKind",
    "TestCase",
    "TestKind",
    "Trace",
    "evaluate_gates",
    "load_config",
]
