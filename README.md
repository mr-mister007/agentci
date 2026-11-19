# AgentCI

**GitHub Actions for AI agents — test, evaluate, gate, deploy.**

AgentCI generates tests for your agent, runs them in a deterministic sandbox, scores the
traces, and blocks the pull request when a metric regresses. Observability tools tell you
what happened in production. AgentCI tells you *before* you ship.

## Quickstart

```bash
uv sync
uv run agentci init
uv run agentci generate --from openapi.yaml --out tests/cases.yaml
uv run agentci run --pr 123 --baseline .agentci/baseline.json
uv run agentci gate --report .agentci/report.json
```

Or start the API + dashboard:

```bash
docker compose up --build
# API  → http://localhost:8000/docs
# Web  → http://localhost:5173
```

## Layout

| Path | Purpose |
| --- | --- |
| `packages/core` | Shared models, YAML config, gate engine |
| `packages/runner` | Deterministic simulation engine, trace capture, replay |
| `packages/eval` | Built-in scorers + custom scorer SDK |
| `packages/testgen` | Test synthesis from OpenAPI, prompts, and logs |
| `packages/cli` | `agentci` command line |
| `packages/api` | FastAPI report / status-check API |
| `apps/web` | React + Vite + Tailwind v4 dashboard |
| `.github/actions/run` | Reusable GitHub Action |
| `examples/demo-agent` | FastAPI agent used by the live demo |

## Why AgentCI

1. **Simulation-first** — mocks for LLMs, tools, APIs, and DBs; seeded and replayable.
2. **Zero-config test generation** — point at an OpenAPI spec or prompt, get hundreds of cases.
3. **Hard gates** — real merge blocks, not dashboards nobody reads.
4. **Cost is a first-class metric** — token budgets enforced per agent.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/QUICKSTART.md](docs/QUICKSTART.md).
