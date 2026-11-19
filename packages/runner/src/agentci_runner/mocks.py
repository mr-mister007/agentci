from __future__ import annotations

import hashlib
import random
from typing import Any

from agentci_core.config import SimulationConfig
from pydantic import BaseModel


class MockRegistry(BaseModel):
    """Seeded, deterministic stand-ins for LLMs, tools, APIs and databases."""

    config: SimulationConfig = SimulationConfig()

    def seed_for(self, test_id: str) -> int:
        digest = hashlib.sha256(f"{self.config.seed}:{test_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big")

    def rng(self, test_id: str) -> random.Random:
        return random.Random(self.seed_for(test_id))

    def latency_ms(self, test_id: str, kind: str = "llm") -> float:
        rng = self.rng(f"{test_id}:{kind}")
        base = {"llm": 120.0, "tool": 18.0, "api": 35.0, "db": 8.0}.get(kind, 10.0)
        return round(base * rng.uniform(0.6, 1.6), 2)

    def tokens_for(self, test_id: str) -> tuple[int, int]:
        rng = self.rng(f"{test_id}:tokens")
        return rng.randint(80, 900), rng.randint(20, 260)

    def tool_response(self, tool: str, arguments: dict[str, Any]) -> Any:
        configured = self.config.tool_mocks.get(tool)
        if configured is not None:
            return configured
        return {"ok": True, "tool": tool, "arguments": arguments, "mocked": True}

    def payload(self) -> dict[str, Any]:
        return {
            "llm": self.config.llm_mock,
            "tools": self.config.tool_mocks,
            "sandbox": self.config.sandbox,
            "seed": self.config.seed,
        }
