from __future__ import annotations

from agentci_core.models import Span, SpanKind, TestCase, TestKind, Trace
from agentci_eval import evaluate, summarize
from agentci_eval.registry import available_scorers, register


def _case(**expected: object) -> TestCase:
    return TestCase(
        name="case",
        kind=TestKind.HAPPY,
        input={"value": "hi"},
        expected=expected,
    )


def _trace(output: dict[str, object], **kwargs: object) -> Trace:
    span = Span(
        kind=SpanKind.LLM,
        name="llm",
        duration_ms=100,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        output=output,
    )
    return Trace(test_id="", spans=[span], output=output, **kwargs)


def _score(case: TestCase, trace: Trace, names: list[str]):
    trace.test_id = case.id
    return evaluate([case], [trace], names)[0]


def test_exact_match_and_schema() -> None:
    case = _case(
        output={"answer": "hello"},
        json_schema={"type": "object", "required": ["answer"]},
    )
    result = _score(case, _trace({"answer": "hello"}), ["exact_match", "json_schema"])
    assert result.passed
    assert {metric.name for metric in result.metrics} == {"exact_match", "json_schema"}


def test_schema_failure_is_reported() -> None:
    case = _case(json_schema={"type": "object", "required": ["answer"]})
    result = _score(case, _trace({"wrong": "shape"}), ["json_schema"])
    assert not result.passed
    assert result.failure_reason and "json_schema" in result.failure_reason


def test_safety_detects_pii() -> None:
    case = _case()
    trace = _trace({"answer": "email me at jane.doe@example.com"})
    result = _score(case, trace, ["safety"])
    assert not result.passed


def test_custom_scorer_registration() -> None:
    @register("starts_with_h")
    def starts_with_h(case: TestCase, trace: Trace) -> bool:
        return str(trace.output.get("answer", "")).startswith("h")

    assert "starts_with_h" in available_scorers()
    case = _case()
    result = _score(case, _trace({"answer": "hello"}), ["starts_with_h"])
    assert result.passed


def test_summarize_computes_regressions_and_cost_delta() -> None:
    cases = [_case(), _case()]
    traces = [_trace({"answer": "a"}), _trace({"answer": "b"})]
    for case, trace in zip(cases, traces, strict=True):
        trace.test_id = case.id
    results = evaluate(cases, traces, [])
    summary = summarize(results, "agent")
    assert summary.pass_rate == 1.0
    assert summary.total_cost_usd == 0.002
    assert summary.p95_latency_ms == 100
