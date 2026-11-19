from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentci_core.models import EvalResult, RunSummary


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 2)


def _mean_metric(results: list[EvalResult], name: str) -> float:
    values = [
        metric.value for result in results for metric in result.metrics if metric.name == name
    ]
    return round(sum(values) / len(values), 4) if values else 1.0


def summarize(
    results: list[EvalResult],
    agent: str,
    baseline: RunSummary | None = None,
    **meta: Any,
) -> RunSummary:
    total = len(results)
    passed = sum(result.passed for result in results)
    latencies = [result.latency_ms for result in results]
    total_cost = round(sum(result.cost_usd for result in results), 6)
    cost_per_run = total_cost / max(total, 1)
    baseline_cost_per_run = (
        baseline.total_cost_usd / max(baseline.total, 1) if baseline is not None else cost_per_run
    )

    baseline_passed = (
        {result.test_id for result in baseline.results if result.passed} if baseline else set()
    )
    regressions = (
        sum(1 for result in results if not result.passed and result.test_id in baseline_passed)
        if baseline
        else 0
    )

    return RunSummary(
        agent=agent,
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=round(passed / max(total, 1), 4),
        p50_latency_ms=_percentile(latencies, 0.5),
        p95_latency_ms=_percentile(latencies, 0.95),
        total_cost_usd=total_cost,
        cost_delta_usd=round(cost_per_run - baseline_cost_per_run, 6),
        tool_accuracy=_mean_metric(results, "tool_accuracy"),
        safety_score=_mean_metric(results, "safety"),
        policy_score=_mean_metric(results, "policy"),
        regressions=regressions,
        baseline_run_id=baseline.run_id if baseline else None,
        finished_at=datetime.now(UTC),
        results=results,
        **meta,
    )
