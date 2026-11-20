# Production deployment (Docker/VPS)

AgentCI ships as a Docker Compose stack: Caddy, Postgres, Redis, a one-shot Alembic migration
job, the FastAPI API, an `arq` worker, and a static dashboard. Only Caddy publishes ports
80/443; every other service stays on the private `agentci` Docker network.

## 1. Configure

```bash
cp .env.example .env
```

At minimum set:

| Variable | Description |
| --- | --- |
| `AGENTCI_DOMAIN` | Public hostname, e.g. `agentci.example.com`. |
| `AGENTCI_TLS_MODE` | ACME contact email for real domains, or `internal` for local self-signed certs. |
| `AGENTCI_PUBLIC_API_URL` | Browser-facing API URL, normally `https://<domain>/api`. |
| `AGENTCI_POSTGRES_PASSWORD` | Strong Postgres password. |
| `AGENTCI_BOOTSTRAP_API_KEY` | Admin token and first org API key. Generate with `openssl rand -hex 32`. |
| `AGENTCI_GITHUB_WEBHOOK_SECRET` | HMAC secret for `POST /api/webhooks/github`. |
| `AGENTCI_REPORT_URL` | Base URL used as the GitHub status `target_url`. |

Optional:

| Variable | Default | Description |
| --- | --- | --- |
| `AGENTCI_BOOTSTRAP_ORG_NAME` | `default` | Name for the bootstrap org. |
| `AGENTCI_MONTHLY_BUDGET_USD` | `25` | Default monthly budget for new orgs. |
| `AGENTCI_MONTHLY_RUN_QUOTA` | `10000` | Default monthly run quota for new orgs. |
| `AGENTCI_ARTIFACT_BACKEND` | `local` | `local` or `s3`. |
| `AGENTCI_ALLOW_PRIVATE_ENDPOINTS` | `0` | Set to `1` only for trusted internal agents. |
| `AGENTCI_ALLOW_INSECURE_AGENT_ENDPOINTS` | `0` | Set to `1` only for isolated test deployments. |
| `AGENTCI_GRAFANA_PASSWORD` | `admin` | Change before enabling the monitoring profile. |

CORS is derived from `AGENTCI_DOMAIN`. For multiple browser origins, edit the API service's
`AGENTCI_CORS_ORIGINS` value in `docker-compose.yml`.

For local testing with a self-signed certificate, keep `AGENTCI_DOMAIN=localhost`,
`AGENTCI_TLS_MODE=internal`, and `AGENTCI_PUBLIC_API_URL=https://localhost/api`. Your browser
will show a certificate warning for the local CA.

## 2. Start

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f api worker caddy
```

The `migrate` service applies Alembic migrations and exits. The API and worker only start
after it succeeds. Caddy then serves:

- `https://<domain>` → dashboard
- `https://<domain>/api` → API
- `/api/metrics` and `/metrics` are blocked at the proxy; scrape `api:8000/metrics` from the
  private network instead.

Internal health checks:

```bash
docker compose exec api curl -fsS http://127.0.0.1:8000/health
docker compose exec api curl -fsS http://127.0.0.1:8000/ready
docker compose exec api curl -fsS http://127.0.0.1:8000/metrics | head
```

## 3. Provision organizations and keys

```bash
curl -sX POST https://agentci.example.com/api/orgs \
  -H "X-Admin-Token: $AGENTCI_BOOTSTRAP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme","slug":"acme"}'
```

The response contains a one-time `api_key`. Store it in your secret manager and use it in the
dashboard and CI. Additional keys and revocation:

```bash
curl -s https://agentci.example.com/api/orgs/<org-id>/keys \
  -H "X-Admin-Token: $AGENTCI_BOOTSTRAP_API_KEY"

curl -sX POST https://agentci.example.com/api/orgs/<org-id>/keys \
  -H "X-Admin-Token: $AGENTCI_BOOTSTRAP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci"}'

curl -sX DELETE https://agentci.example.com/api/orgs/<org-id>/keys/<key-id> \
  -H "X-Admin-Token: $AGENTCI_BOOTSTRAP_API_KEY"
```

Rotate the bootstrap key by changing `.env` and recreating the API container. Revoke old
org keys explicitly with the endpoint above.

## 4. Artifacts

Local disk is the default and is stored in the `agentci_agentci-artifacts` volume. For S3:

```bash
AGENTCI_ARTIFACT_BACKEND=s3
AGENTCI_S3_BUCKET=agentci-artifacts
AGENTCI_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com
AGENTCI_S3_REGION=us-east-1
AGENTCI_S3_ACCESS_KEY=...
AGENTCI_S3_SECRET_KEY=...
```

Artifacts are `report.json` and `traces.jsonl`, written per run. `GET /api/runs/{id}/artifacts`
returns presigned URLs (or `file://` URIs for local disk). Enable bucket versioning and a
lifecycle policy in production.

## 5. LLM test generation

Any OpenAI-compatible chat-completions API works:

```bash
AGENTCI_LLM_API_KEY=sk-...
AGENTCI_LLM_BASE_URL=https://api.openai.com/v1
AGENTCI_LLM_MODEL=gpt-4o-mini
AGENTCI_LLM_TIMEOUT_SECONDS=60
```

These are process-level environment variables, not per-tenant secrets. Without a key,
`--generator llm` falls back to deterministic OpenAPI/prompt/log generators.

## 6. Security model

- API keys are stored as SHA-256 hashes; plaintext is shown only when created.
- Every run, report, job, artifact URL, and baseline lookup is scoped to the authenticated org.
- Private and loopback agent endpoints are blocked unless `allow_private_endpoint` is explicitly
  enabled in YAML or `AGENTCI_ALLOW_PRIVATE_ENDPOINTS=1`.
- Docker/gVisor sandboxes run with no capabilities, `no-new-privileges`, a read-only root
  filesystem, tmpfs `/tmp`, and memory/CPU/PID limits.
- Firecracker is not implemented; `create_sandbox` rejects it rather than faking isolation.
- GitHub webhooks are HMAC-SHA256 verified when `AGENTCI_GITHUB_WEBHOOK_SECRET` is set.
- Rate limits are enforced per org and route; quota and budget checks happen before a run.
- Containers run with `no-new-privileges`, log rotation, CPU/memory limits, and `restart: unless-stopped`.
- API docs are disabled in prod; metrics are not publicly proxied.

Use a firewall that exposes only 80/443 on the VPS. Keep Postgres and Redis on the private
Docker network, and terminate TLS at Caddy or your own reverse proxy.

## 7. Monitoring and alerting

```bash
docker compose --profile monitoring up -d
```

Prometheus listens on `127.0.0.1:9090`, Grafana on `127.0.0.1:3000`, and the datasource is
provisioned automatically. Prometheus scrapes API metrics from `api:8000/metrics` and worker
metrics from `worker:8001/metrics`. Alert rules live in `monitoring/alerts.rules.yml`:

- 5xx rate above 5% for 5 minutes
- p95 latency above 2 seconds for 10 minutes
- queue depth above 50 for 10 minutes
- more than 10 failed gates in an hour

Key metrics: `agentci_requests_total`, `agentci_request_duration_seconds`,
`agentci_runs_total`, `agentci_agent_calls_total`, `agentci_run_cost_usd`, and
`agentci_queue_depth`. Restrict 9090/3000 to your monitoring network or SSH tunnel.

## 8. Backup and restore

```bash
scripts/backup.sh ./backups/agentci-20261119-120000
scripts/restore.sh ./backups/agentci-20261119-120000
```

The backup contains a gzipped Postgres dump and a tar of the artifact volume. Copy it off the
VPS. For scheduled backups, add a cron entry or systemd timer calling `scripts/backup.sh`.
Restore stops the API and worker, replaces the database and artifact volume contents, then
restarts them.

## 9. API surface

All paths below are prefixed with `/api` at the public proxy.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | none | Liveness. |
| `GET` | `/ready` | none | Postgres and Redis readiness. |
| `GET` | `/metrics` | none | Prometheus metrics; private network only. |
| `POST` | `/orgs` | admin | Create org + API key. |
| `GET` | `/orgs/{id}/keys` | admin | List key metadata. |
| `POST` | `/orgs/{id}/keys` | admin | Add an API key. |
| `DELETE` | `/orgs/{id}/keys/{key_id}` | admin | Revoke an API key. |
| `POST` | `/runs` | org key | Store a completed report. |
| `POST` | `/runs/async` | org key | Queue an evaluation. |
| `GET` | `/jobs/{id}` | org key | Poll a queued job. |
| `GET` | `/runs` | org key | List reports, optionally `?pr=`. |
| `GET` | `/runs/{id}` | org key | Read a report. |
| `GET` | `/runs/{id}/artifacts` | org key | Presigned artifact URLs. |
| `GET` | `/runs/{id}/trace/{test_id}` | org key | Replay a trace. |
| `GET` | `/pr/{pr}/status` | org key | Latest PR gate status. |
| `POST` | `/webhooks/github` | HMAC | Webhook summary for a PR. |

## 10. Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy packages
uv run pytest
cd apps/web && npm run build
AGENTCI_DOMAIN=localhost AGENTCI_POSTGRES_PASSWORD=test AGENTCI_BOOTSTRAP_API_KEY=test \
  docker compose config -q
```

The test suite covers the repository/auth layer, quotas, endpoint validation, retry policy,
Docker sandbox argument construction, artifact stores, rate limiting, metrics, LLM generation,
and authenticated API paths.

## 11. Real-world validation

The full Docker stack was exercised end to end against a real FastAPI agent running as a
separate container. TLS termination, org provisioning, the dashboard, the queue worker,
Postgres persistence, artifact storage, backup, trace replay, Prometheus, and Grafana were all
part of the run.

- Generated 17 cases from `openapi.yaml`; the healthy agent passed 17/17 with all gates green.
- A deliberately broken agent was compared against the saved baseline: 16/17 passed, gates
  failed on `pass_rate` and `regressions`, and the CLI exited non-zero.
- An async run was queued through the public API and completed by the worker; the report and
  artifacts were stored and retrievable through the API.
- Worker metrics are scraped separately from API metrics so `agentci_runs_total`,
  `agentci_agent_calls_total`, and `agentci_run_cost_usd` are visible in Prometheus.

GitHub commit statuses and real LLM-backed generation were not exercised because they require
external credentials.
