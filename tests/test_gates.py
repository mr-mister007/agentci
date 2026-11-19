from __future__ import annotations

import json
from pathlib import Path

from agentci_core.config import GateConfig
from agentci_core.gates import evaluate_gates
from agentci_core.models import RunSummary


def _summary(**kwargs: object) -> RunSummary:
    defaults: dict[str, object] = {
        "agent": "test",
        "total": 10,
        "passed": 9,
        "pass_rate": 0.9,
        "p95_latency_ms": 900,
    }
    return RunSummary(**{**defaults, **kwargs})


def test_gates_pass_when_thresholds_met() -> None:
    decision = evaluate_gates(
        _summary(),
        [GateConfig(metric="pass_rate", min=0.9), GateConfig(metric="p95_latency_ms", max=1000)],
    )
    assert decision.passed
    assert decision.status == "success"
    assert len(decision.results) == 2


def test_gates_fail_with_reason() -> None:
    decision = evaluate_gates(_summary(), [GateConfig(metric="pass_rate", min=0.95)])
    assert not decision.passed
    assert decision.status == "failure"
    assert "pass_rate" in decision.summary


def test_change_max_uses_baseline() -> None:
    baseline = _summary(p95_latency_ms=800)
    decision = evaluate_gates(
        _summary(p95_latency_ms=900),
        [GateConfig(metric="p95_latency_ms", change_max=50)],
        baseline=baseline,
    )
    assert not decision.passed
    assert decision.results[0].actual == 100.0


def test_auto_approve_requires_passing_gates() -> None:
    gates = [GateConfig(metric="pass_rate", min=0.9)]
    assert evaluate_gates(_summary(), gates, auto_approve=True).auto_approve
    assert not evaluate_gates(
        _summary(), [GateConfig(metric="pass_rate", min=0.99)], auto_approve=True
    ).auto_approve


def test_report_roundtrip(tmp_path: Path) -> None:
    from agentci_core.models import Report

    report = Report(summary=_summary(), gate_passed=True)
    path = tmp_path / "report.json"
    path.write_text(report.model_dump_json())
    assert Report.model_validate_json(path.read_text()).summary.run_id == report.summary.run_id
    assert json.loads(path.read_text())["summary"]["pass_rate"] == 0.9
