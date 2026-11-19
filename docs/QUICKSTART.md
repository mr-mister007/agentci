# Quickstart

## Local

```bash
uv sync
uv run agentci init
uv run agentci generate --from openapi.yaml --out tests/cases.yaml --count 100

# start the sample agent in another terminal
uv run uvicorn examples.demo_agent.app:app --app-dir examples/demo-agent --port 8000

AGENTCI_ALLOW_PRIVATE_ENDPOINTS=1 uv run agentci run --pr 123 --baseline-out .agentci/baseline.json
uv run agentci replay <test-id> --report .agentci/report.json
```

`AGENTCI_ALLOW_PRIVATE_ENDPOINTS=1` is required because the sample agent listens on
loopback. Public HTTPS endpoints do not need it.

`agentci run` exits non-zero when a gate fails, so it drops straight into CI.

## Configuration

Everything lives in `agentci.yaml`:

- `agent.endpoint` — HTTP endpoint that accepts `{test_id, input, expected, seed, mocks}`.
- `simulation.seed` / `concurrency` — deterministic mocks and parallelism.
- `sources` — OpenAPI specs, prompt files, JSONL production logs, or YAML case files.
- `gates` — thresholds such as `pass_rate >= 0.95`, `p95_latency_ms <= 1500`, `regressions <= 0`.
- `baseline.auto_approve` — approve the PR only when every gate is green.

Override the endpoint in CI with `AGENTCI_ENDPOINT`.

## Custom scorers

```python
from agentci_eval import register
from agentci_core.models import TestCase, Trace


@register("brand_voice")
def brand_voice(case: TestCase, trace: Trace) -> float:
    return 1.0 if "sorry" not in str(trace.output).lower() else 0.0
```

Then add `brand_voice` to `scorers:` in `agentci.yaml`.

## GitHub Action

```yaml
- uses: agentci/agentci/.github/actions/run@v1
  with:
    pr: ${{ github.event.pull_request.number }}
    agent-url: http://localhost:8000/invoke
```

The action posts an `AgentCI` status check with pass rate, p95, cost, and regressions.

## Dashboard

```bash
export AGENTCI_BOOTSTRAP_API_KEY="$(openssl rand -hex 32)"
uv run agentci-api      # http://localhost:8000/docs
cd apps/web && npm install && npm run dev   # http://localhost:5173
```

Create an org/key with the bootstrap token, then paste the `api_key` into the dashboard:

```bash
curl -sX POST http://localhost:8000/orgs \
  -H "X-Admin-Token: $AGENTCI_BOOTSTRAP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Local","slug":"local"}'
```

Or `docker compose up --build` for the full Postgres + Redis + worker stack.
