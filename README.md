# AgentCI

**GitHub Actions for AI agents — test, evaluate, gate, deploy.**

AgentCI generates tests for your agent, runs them in a deterministic sandbox, scores the
traces, and blocks the pull request when a metric regresses. Observability tools tell you
what happened in production. AgentCI tells you *before* you ship.

## Requirements

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- Docker (only for Docker/gVisor sandboxes and the production stack)
- Node.js 22+ (only for dashboard development)

## Quickstart

```bash
uv sync
uv run agentci init
uv run agentci generate --from openapi.yaml --out tests/cases.yaml
AGENTCI_ALLOW_PRIVATE_ENDPOINTS=1 uv run agentci run --pr 123
uv run agentci gate --report .agentci/report.json
uv run agentci report .agentci/report.json
```

`AGENTCI_ALLOW_PRIVATE_ENDPOINTS=1` is only needed when the agent under test is on a private
or loopback address, such as the local demo agent. Public HTTPS endpoints work without it.

### CLI

| Command | Purpose |
| --- | --- |
| `agentci init` | Create `agentci.yaml` and a sample suite. |
| `agentci generate` | Generate cases from OpenAPI, prompts, logs, or an LLM. |
| `agentci run` | Simulate the agent, score traces, and enforce gates. |
| `agentci gate` | Re-evaluate gates from an existing report. |
| `agentci report` | Print a saved report. |
| `agentci replay` | Replay a captured trace. |
| `agentci validate` | Validate a case file. |
| `agentci status` | Poll an async run. |
| `agentci worker` | Start the API queue worker locally. |

`agentci run` also supports `--async --api-url ... --api-key ...` to enqueue work on the
production API instead of running locally.

LLM-backed test generation uses any OpenAI-compatible chat-completions endpoint:

```bash
export AGENTCI_LLM_API_KEY=sk-...
export AGENTCI_LLM_BASE_URL=https://api.openai.com/v1
export AGENTCI_LLM_MODEL=gpt-4o-mini
uv run agentci generate --from prompts/support.txt --generator llm --count 50
```

If the LLM key or endpoint is unavailable, generation falls back to the deterministic
heuristic generators.

## GitHub Action

```yaml
- uses: mr-mister007/agentci/.github/actions/run@main
  with:
    pr: ${{ github.event.pull_request.number }}
    agent-url: ${{ secrets.AGENT_URL }}
    api-url: ${{ secrets.AGENTCI_API_URL }}
    api-key: ${{ secrets.AGENTCI_API_KEY }}
    baseline: .agentci/baseline.json
    fail-on-gate: "true"
```

The action generates cases from an `openapi.yaml` when present, runs the suite, enforces
gates, and posts an `AgentCI` commit status with pass rate, p95 latency, cost, and
regressions. Omit `api-url`/`api-key` to keep reports local to the workflow.

## Production stack

```bash
cp .env.example .env
# edit .env and set AGENTCI_BOOTSTRAP_API_KEY
docker compose up --build
# API      → http://localhost:8000  (docs disabled in prod)
# Metrics  → http://localhost:8000/metrics
# Web      → http://localhost:4173
```

The stack runs Postgres, Redis, the API, an `arq` worker, and the dashboard. The API applies
Alembic migrations on boot; the worker executes queued evaluations and writes reports,
artifacts, quota usage, and GitHub commit statuses. Postgres, Redis, and the artifact volume
are persistent; Redis only holds transient job and rate-limit state.

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

## Local API and dashboard

```bash
uv run agentci-api
# API docs → http://localhost:8000/docs

cd apps/web
npm install
npm run dev   # http://localhost:5173
```

Create an organization and API key using the bootstrap token, then sign in to the dashboard:

```bash
curl -sX POST http://localhost:8000/orgs \
  -H "X-Admin-Token: $AGENTCI_BOOTSTRAP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Local","slug":"local"}'
```

## Configuration

Behavior lives in `agentci.yaml`: agent endpoint, simulation seed/concurrency, test sources,
scorers, gates, and baseline approval. Production settings come from environment variables;
see [`.env.example`](.env.example) and [docs/PRODUCTION.md](docs/PRODUCTION.md). LLM test
generation accepts any OpenAI-compatible endpoint and falls back to deterministic generators
when unavailable.

## Development

```bash
uv run ruff format --check packages tests
uv run ruff check packages tests
uv run mypy packages
uv run pytest
cd apps/web && npm run build
```

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
