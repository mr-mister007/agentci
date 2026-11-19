from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class AgentConfig(BaseModel):
    name: str = "agent"
    endpoint: str = "http://localhost:8000/invoke"
    auth_env: str | None = None
    timeout_seconds: float = 30.0
    request_template: dict[str, Any] = Field(default_factory=dict)


class SimulationConfig(BaseModel):
    seed: int = 42
    concurrency: int = 8
    llm_mock: Literal["deterministic", "recorded", "live"] = "deterministic"
    tool_mocks: dict[str, Any] = Field(default_factory=dict)
    sandbox: Literal["inprocess", "gvisor", "firecracker"] = "inprocess"


class SourceConfig(BaseModel):
    path: str | None = None
    generator: Literal["openapi", "prompt", "logs", "yaml", None] = None
    count: int = 20


class GateConfig(BaseModel):
    metric: str
    min: float | None = None
    max: float | None = None
    change_max: float | None = None
    description: str = ""

    @field_validator("metric")
    @classmethod
    def _known_metric(cls, value: str) -> str:
        allowed = {
            "pass_rate",
            "p95_latency_ms",
            "p50_latency_ms",
            "cost_per_run_usd",
            "cost_delta_usd",
            "tool_accuracy",
            "safety_score",
            "policy_score",
            "regressions",
            "failed",
        }
        if value not in allowed:
            raise ValueError(f"Unknown gate metric {value!r}; expected one of {sorted(allowed)}")
        return value


class BaselineConfig(BaseModel):
    path: str | None = None
    run_id: str | None = None
    auto_approve: bool = True


class LoadedConfig(BaseModel):
    version: int = 1
    agent: AgentConfig = Field(default_factory=AgentConfig)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    sources: list[SourceConfig] = Field(default_factory=list)
    gates: list[GateConfig] = Field(default_factory=list)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)
    scorers: list[str] = Field(
        default_factory=lambda: [
            "exact_match",
            "json_schema",
            "tool_accuracy",
            "latency",
            "token_cost",
            "safety",
            "policy",
        ]
    )
    root: Path = Field(default_factory=Path.cwd)

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.root / candidate


DEFAULT_CONFIG = """\
version: 1
agent:
  name: my-agent
  endpoint: http://localhost:8000/invoke
  timeout_seconds: 30
simulation:
  seed: 42
  concurrency: 8
  llm_mock: deterministic
  sandbox: inprocess
sources:
  - generator: openapi
    path: openapi.yaml
    count: 50
gates:
  - metric: pass_rate
    min: 0.95
  - metric: p95_latency_ms
    max: 1500
  - metric: cost_per_run_usd
    max: 0.05
  - metric: tool_accuracy
    min: 0.9
  - metric: safety_score
    min: 1.0
  - metric: regressions
    max: 0
baseline:
  auto_approve: true
"""


def load_config(path: str | Path | None = None) -> LoadedConfig:
    """Load and validate an agentci.yaml file, falling back to safe defaults."""
    if path is None:
        for candidate in (Path("agentci.yaml"), Path("agentci.yml")):
            if candidate.exists():
                path = candidate
                break
    if path is None or not Path(path).exists():
        return LoadedConfig()

    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path} must contain a YAML mapping")

    sources = raw.get("sources")
    if sources is None:
        sources = []
        if "tests" in raw:
            sources.extend({"generator": "yaml", "path": p} for p in raw["tests"])
        for kind in ("openapi", "prompt", "logs"):
            if kind in raw:
                entry = raw[kind]
                entry = {"path": entry} if isinstance(entry, str) else dict(entry)
                sources.append({"generator": kind, **entry})

    loaded = LoadedConfig.model_validate(
        {
            **raw,
            "sources": sources,
            "root": config_path.parent,
        }
    )
    if endpoint := os.environ.get("AGENTCI_ENDPOINT"):
        loaded.agent.endpoint = endpoint
    return loaded
