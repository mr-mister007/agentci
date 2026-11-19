# Production deployment

AgentCI ships as four services: FastAPI API, `arq` worker, Postgres, and Redis. The dashboard
is a static Vite build served by `vite preview` in the provided image; put it behind your own
CDN or reverse proxy for real deployments.

## Required configuration

| Variable | Required | Description |
| --- | --- | --- |
| `AGENTCI_BOOTSTRAP_API_KEY` | yes | Admin token and first org API key. Rotate after provisioning. |
| `AGENTCI_BOOTSTRAP_ORG_NAME` | no | Name for the bootstrap org. Default `default`. |
| `AGENTCI_DATABASE_URL` | yes | `postgresql+asyncpg://user:pass@host/db` |
| `AGENTCI_REDIS_URL` | yes | Redis URL used by the queue and distributed rate limiter. |
| `AGENTCI_CORS_ORIGINS` | yes | JSON list, e.g. `["https://agentci.example.com"]`. |
| `AGENTCI_ENVIRONMENT` | no | Set `prod` to disable OpenAPI docs and auto-table creation. |
| `AGENTCI_MONTHLY_BUDGET_USD` | no | Default monthly budget for new orgs. |
| `AGENTCI_MONTHLY_RUN_QUOTA` | no | Default monthly run quota for new orgs. |
| `AGENTCI_ALLOW_INSECURE_AGENT_ENDPOINTS` | no | Leave unset in production. |
| `AGENTCI_ALLOW_PRIVATE_ENDPOINTS` | no | Allow agent endpoints on private/loopback IPs. |
| `AGENTCI_GITHUB_WEBHOOK_SECRET` | recommended | HMAC secret for `POST /webhooks/github`. |
| `AGENTCI_REPORT_URL` | no | Base URL used as the GitHub status `target_url`. |

### Artifact storage

Local disk is the default:

```bash
AGENTCI_ARTIFACT_BACKEND=local
AGENTCI_ARTIFACT_ROOT=/app/.agentci/artifacts
```

For S3-compatible storage:

```bash
AGENTCI_ARTIFACT_BACKEND=s3
AGENTCI_S3_BUCKET=agentci-artifacts
AGENTCI_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com
AGENTCI_S3_REGION=us-east-1
AGENTCI_S3_ACCESS_KEY=...
AGENTCI_S3_SECRET_KEY=...
```

Artifacts are `report.json` and `traces.jsonl`, written per run. `GET /runs/{id}/artifacts`
returns presigned URLs (or `file://` URIs for local disk).

### LLM test generation

Any OpenAI-compatible chat-completions API works:

```bash
AGENTCI_LLM_API_KEY=sk-...
AGENTCI_LLM_BASE_URL=https://api.openai.com/v1
AGENTCI_LLM_MODEL=gpt-4o-mini
AGENTCI_LLM_TIMEOUT_SECONDS=60
```

Without a key, `--generator llm` falls back to deterministic OpenAPI/prompt/log generators.

## Security model

- API keys are stored as SHA-256 hashes. Plaintext keys are shown once when created.
- Every run, report, job, artifact URL, and baseline lookup is scoped to the authenticated
  organization.
- Agent endpoints are validated before calls. Private and loopback addresses are blocked
  unless `allow_private_endpoint` is explicitly enabled in YAML or via env.
- Docker/gVisor sandboxes run with no capabilities, `no-new-privileges`, a read-only root
  filesystem, tmpfs `/tmp`, memory/CPU/PID limits, and an explicit environment allowlist.
- Firecracker is not implemented; `create_sandbox` rejects it rather than pretending to
  provide VM isolation.
- Webhook requests are verified with HMAC-SHA256 when `AGENTCI_GITHUB_WEBHOOK_SECRET` is set.
- Rate limits are enforced per org and route (60 requests/minute by default), using Redis
  when configured.
- Monthly run quota and budget checks happen before a run is accepted.

Put the API behind TLS and a reverse proxy. Restrict `/metrics` to your monitoring network.
Run Postgres and Redis on private networks only.

## API surface

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | none | Liveness. |
| `GET` | `/ready` | none | Postgres and Redis readiness. |
| `GET` | `/metrics` | none | Prometheus metrics. |
| `POST` | `/orgs` | admin | Create org + API key. |
| `POST` | `/orgs/{id}/keys` | admin | Rotate/add an API key. |
| `POST` | `/runs` | org key | Store a completed report. |
| `POST` | `/runs/async` | org key | Queue an evaluation. |
| `GET` | `/jobs/{id}` | org key | Poll a queued job. |
| `GET` | `/runs` | org key | List reports, optionally `?pr=`. |
| `GET` | `/runs/{id}` | org key | Read a report. |
| `GET` | `/runs/{id}/artifacts` | org key | Presigned artifact URLs. |
| `GET` | `/runs/{id}/trace/{test_id}` | org key | Replay a trace. |
| `GET` | `/pr/{pr}/status` | org key | Latest PR gate status. |
| `POST` | `/webhooks/github` | HMAC | Webhook summary for a PR. |

## Operations

Run migrations manually when you do not want the container entrypoint to do it:

```bash
alembic upgrade head
```

Start the worker:

```bash
agentci-worker
# or: python -m arq agentci_api.worker.WorkerSettings
```

Health checks:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
curl -fsS http://localhost:8000/metrics | head
```

Key metrics: `agentci_requests_total`, `agentci_request_duration_seconds`,
`agentci_runs_total`, `agentci_agent_calls_total`, `agentci_run_cost_usd`, and
`agentci_queue_depth`.

Queue jobs are stored in Redis for 24 hours. Completed reports are durable in Postgres.
Artifacts are durable in S3 or the configured local volume. Back up Postgres and the artifact
bucket; Redis holds transient state only.

## Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy packages
uv run pytest
cd apps/web && npm run build
```

The test suite covers the repository/auth layer, quotas, endpoint validation, retry policy,
Docker sandbox argument construction, artifact stores, rate limiting, metrics, LLM generation,
and the authenticated API paths.
