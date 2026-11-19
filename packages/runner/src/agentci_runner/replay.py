from __future__ import annotations

from typing import Any

from agentci_core.models import Trace


def replay_trace(trace: Trace) -> dict[str, Any]:
    """Build a deterministic, seekable timeline from a captured trace."""
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for span in trace.spans:
        start = span.start_ms or cursor
        timeline.append(
            {
                "at_ms": round(start, 2),
                "kind": span.kind.value,
                "name": span.name,
                "duration_ms": span.duration_ms,
                "input": span.input,
                "output": span.output,
                "tokens": span.tokens_in + span.tokens_out,
                "cost_usd": span.cost_usd,
                "error": span.error,
            }
        )
        cursor = start + span.duration_ms

    return {
        "trace_id": trace.trace_id,
        "test_id": trace.test_id,
        "status": trace.status,
        "error": trace.error,
        "duration_ms": trace.duration_ms,
        "total_tokens": trace.total_tokens,
        "total_cost_usd": round(trace.total_cost_usd, 6),
        "output": trace.output,
        "timeline": timeline,
    }
