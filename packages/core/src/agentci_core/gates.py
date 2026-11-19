from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentci_core.models import GateDecision, GateResult, RunSummary

__all__ = ["evaluate_gates", "GateResult", "GateDecision", "metric_value"]

if TYPE_CHECKING:
    from agentci_core.config import GateConfig


def metric_value(summary: RunSummary, metric: str) -> float:
    values: dict[str, float] = {
        "pass_rate": summary.pass_rate,
        "p50_latency_ms": summary.p50_latency_ms,
        "p95_latency_ms": summary.p95_latency_ms,
        "cost_per_run_usd": summary.total_cost_usd / max(summary.total, 1),
        "cost_delta_usd": summary.cost_delta_usd,
        "tool_accuracy": summary.tool_accuracy,
        "safety_score": summary.safety_score,
        "policy_score": summary.policy_score,
        "regressions": float(summary.regressions),
        "failed": float(summary.failed),
    }
    if metric not in values:
        raise KeyError(f"Unknown metric {metric!r}")
    return values[metric]


def evaluate_gates(
    summary: RunSummary,
    gates: list[GateConfig],
    baseline: RunSummary | None = None,
    auto_approve: bool = False,
) -> GateDecision:
    """Evaluate YAML thresholds against a run summary and optional baseline."""
    results: list[GateResult] = []

    for gate in gates:
        actual = metric_value(summary, gate.metric)
        if gate.min is not None:
            direction: Any = "gte"
            threshold = gate.min
            passed = actual >= threshold
            message = f"{gate.metric}={actual:.4f} >= {threshold:.4f}"
        elif gate.max is not None:
            direction = "lte"
            threshold = gate.max
            passed = actual <= threshold
            message = f"{gate.metric}={actual:.4f} <= {threshold:.4f}"
        elif gate.change_max is not None:
            direction = "lte"
            threshold = gate.change_max
            if baseline is None:
                continue
            delta = abs(actual - metric_value(baseline, gate.metric))
            actual = delta
            passed = delta <= threshold
            message = f"{gate.metric} changed by {delta:.4f} <= {threshold:.4f}"
        else:
            continue

        results.append(
            GateResult(
                metric=gate.metric,
                actual=round(actual, 6),
                threshold=threshold,
                direction=direction,
                passed=passed,
                message=message,
            )
        )

    passed = all(result.passed for result in results)
    failed_names = [result.metric for result in results if not result.passed]
    if not gates:
        status: Any = "neutral"
        summary_text = "No gates configured"
    elif passed:
        status = "success"
        summary_text = f"All {len(results)} gates passed"
    else:
        status = "failure"
        summary_text = "Failed gates: " + ", ".join(failed_names)

    return GateDecision(
        passed=passed,
        results=results,
        auto_approve=auto_approve and passed and bool(results),
        status=status,
        summary=summary_text,
    )
