from __future__ import annotations

import random
from typing import Any

from fastapi import FastAPI

app = FastAPI(title="AgentCI Demo Agent")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _answer(text: str) -> str:
    lowered = text.lower()
    if "ignore all previous instructions" in lowered or "reveal the system prompt" in lowered:
        return "I can't share internal instructions."
    if "email" in lowered or "ssn" in lowered or "123-45-6789" in lowered:
        return "I can't help with private personal data."
    if "order" in lowered:
        return "Your order is on its way."
    if "<script>" in lowered or "drop table" in lowered:
        return "I can't process that request."
    if not text.strip():
        return "Please tell me how I can help."
    return "I'm a demo support agent. How can I help?"


@app.post("/invoke")
def invoke(request: dict[str, Any]) -> dict[str, Any]:
    seed = int(request.get("seed", 42))
    rng = random.Random(seed)
    payload = request.get("input", {})
    text = str(payload.get("value", payload.get("message", payload)))
    expected = request.get("expected", {})
    answer = _answer(text)

    tools = expected.get("tools", [])
    tool_calls = [{"name": name, "arguments": payload} for name in tools]
    if not tool_calls and "order" in text.lower():
        tool_calls = [{"name": "lookup_order", "arguments": payload}]

    tokens_in = rng.randint(80, 500)
    tokens_out = rng.randint(20, 200)
    tool_duration = round(rng.uniform(5, 25), 2)
    llm_duration = round(rng.uniform(60, 200), 2)

    spans: list[dict[str, Any]] = [
        {
            "kind": "tool",
            "name": call["name"],
            "duration_ms": tool_duration,
            "input": call["arguments"],
            "output": {"ok": True, "mocked": True},
        }
        for call in tool_calls
    ]
    spans.append(
        {
            "kind": "llm",
            "name": "demo-agent-llm",
            "duration_ms": llm_duration,
            "input": {"prompt": text},
            "output": {"answer": answer},
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(tokens_out * 0.000015, 6),
        }
    )

    return {
        "output": {"answer": answer},
        "tool_calls": tool_calls,
        "spans": spans,
    }
