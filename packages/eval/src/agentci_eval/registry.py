from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from agentci_core.models import EvalResult, MetricResult, TestCase, Trace

ScorerFunction = Callable[[TestCase, Trace], Any]


class Scorer(ABC):
    name: str = "scorer"

    @abstractmethod
    def score(self, case: TestCase, trace: Trace) -> list[MetricResult]:
        """Return one or more metrics for a single case/trace pair."""


_REGISTRY: dict[str, Scorer] = {}


class _FunctionScorer(Scorer):
    def __init__(self, name: str, fn: ScorerFunction) -> None:
        self.name = name
        self.fn = fn

    def score(self, case: TestCase, trace: Trace) -> list[MetricResult]:
        result = self.fn(case, trace)
        if isinstance(result, MetricResult):
            return [result]
        if isinstance(result, bool):
            return [MetricResult(name=self.name, value=float(result), passed=result)]
        if isinstance(result, (int, float)):
            value = float(result)
            return [MetricResult(name=self.name, value=value, passed=bool(value))]
        return [MetricResult.model_validate(item) for item in result or []]


def register(name: str) -> Callable[[Any], Any]:
    """Register a scorer class or function under a config-friendly name."""

    def decorator(target: Any) -> Any:
        instance = target() if isinstance(target, type) else _FunctionScorer(name, target)
        if isinstance(target, type):
            instance.name = name
        _REGISTRY[name] = instance
        return target

    return decorator


scorer = register


def available_scorers() -> list[str]:
    import agentci_eval.builtin  # noqa: F401 - registers built-ins

    return sorted(_REGISTRY)


def get_scorer(name: str) -> Scorer:
    import agentci_eval.builtin  # noqa: F401 - registers built-ins

    if name not in _REGISTRY:
        raise KeyError(f"Unknown scorer {name!r}. Available: {', '.join(available_scorers())}")
    return _REGISTRY[name]


def evaluate(
    cases: list[TestCase],
    traces: list[Trace],
    scorer_names: list[str] | None = None,
) -> list[EvalResult]:
    """Score each trace against its test case using the named scorers."""
    import agentci_eval.builtin  # noqa: F401 - registers built-ins

    names = available_scorers() if scorer_names is None else scorer_names
    scorers = [get_scorer(name) for name in names]
    traces_by_id = {trace.test_id: trace for trace in traces}
    results: list[EvalResult] = []

    for case in cases:
        trace = traces_by_id.get(case.id)
        if trace is None:
            results.append(
                EvalResult(
                    test_id=case.id,
                    test_name=case.name,
                    kind=case.kind,
                    passed=False,
                    failure_reason="No trace captured for this test case",
                )
            )
            continue

        latency = trace.duration_ms or sum(span.duration_ms for span in trace.spans)
        metrics: list[MetricResult] = []
        for scorer_instance in scorers:
            try:
                metrics.extend(scorer_instance.score(case, trace))
            except Exception as exc:  # noqa: BLE001 - a broken scorer must not hide the run
                metrics.append(
                    MetricResult(
                        name=scorer_instance.name,
                        value=0.0,
                        passed=False,
                        details={"error": f"{type(exc).__name__}: {exc}"},
                    )
                )

        failed = [metric for metric in metrics if not metric.passed]
        passed = trace.status == "ok" and not failed
        if failed:
            reason = f"{failed[0].name}: {failed[0].details.get('error') or failed[0].value}"
        elif trace.status != "ok":
            reason = trace.error or "Agent returned an error"
        else:
            reason = None

        results.append(
            EvalResult(
                test_id=case.id,
                test_name=case.name,
                kind=case.kind,
                passed=passed,
                metrics=metrics,
                trace_id=trace.trace_id,
                latency_ms=round(latency, 2),
                cost_usd=round(trace.total_cost_usd, 6),
                failure_reason=reason,
            )
        )

    return results
