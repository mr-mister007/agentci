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
AGENTCI_ALLOW_PRIVATE_ENDPOINTS=1 uv run agentci run --pr 123
uv run agentci gate --report .agentci/report.json
```

`AGENTCI_ALLOW_PRIVATE_ENDPOINTS=1` is only needed when the agent under test is on a private
or loopback address, such as the local demo agent. Public HTTPS endpoints work without it.

LLM-backed test generation uses any OpenAI-compatible chat-completions endpoint:

```bash
export AGENTCI_LLM_API_KEY=sk-...
export AGENTCI_LLM_BASE_URL=https://api.openai.com/v1
export AGENTCI_LLM_MODEL=gpt-4o-mini
uv run agentci generate --from prompts/support.txt --generator llm --count 50
```

If the LLM key or endpoint is unavailable, generation falls back to the deterministic
heuristic generators.

## Production stack

```bash
export AGENTCI_BOOTSTRAP_API_KEY="$(openssl rand -hex 32)"
docker compose up --build
# API      → http://localhost:8000  (docs disabled in prod)
# Metrics  → http://localhost:8000/metrics
# Web      → http://localhost:4173
```

The stack runs Postgres, Redis, the API, an `arq` worker, and the dashboard. The API applies
Alembic migrations on boot; the worker executes queued evaluations and writes reports,
artifacts, quota usage, and GitHub commit statuses.

Create the first organization and API key with the bootstrap key:

```bash
curl -sX POST http://localhost:8000/orgs \
  -H "X-Admin-Token: $AGENTCI_BOOTSTRAP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme","slug":"acme"}'
```

Use the returned `api_key` in the dashboard sign-in form and in CI:

```bash
export AGENTCI_API_URL=http://localhost:8000
export AGENTCI_API_KEY=ac_live_...
uv run agentci run --pr 123 --async
uv run agentci status <job_id>
```

See [docs/PRODUCTION.md](docs/PRODUCTION.md) for configuration, security, storage, and
operations details.

## Layout

| Path | Purpose |
| --- | --- |
| `packages/core` | Shared models, YAML config, gate engine |
| `packages/runner` | Deterministic simulation engine, sandboxing, trace replay |
| `packages/eval` | Built-in scorers + custom scorer SDK |
| `packages/testgen` | Test synthesis from OpenAPI, prompts, logs, and LLMs |
| `packages/cli` | `agentci` command line |
| `packages/api` | FastAPI API, auth, Postgres repository, queue worker |
| `apps/web` | React + Vite + Tailwind v4 dashboard |
| `.github/actions/run` | Reusable GitHub Action |
| `examples/demo-agent` | FastAPI agent used by the live demo |

## Why AgentCI

1. **Simulation-first** — mocks for LLMs, tools, APIs, and DBs; seeded and replayable.
2. **Zero-config test generation** — point at an OpenAPI spec or prompt, get hundreds of cases.
3. **Hard gates** — real merge blocks, not dashboards nobody reads.
4. **Cost is a first-class metric** — token budgets enforced per agent.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/QUICKSTART.md](docs/QUICKSTART.md),
and [docs/PRODUCTION.md](docs/PRODUCTION.md).
