# Architecture

```
 Git push / PR ──▶ AgentCI runner ──▶ Gate engine ──▶ GitHub status check
                        │                  ▲
          ┌─────────────┴──────────┐       │
          ▼                        ▼       │
   Test generator          Simulation traces
   (OpenAPI/prompt/logs)   (mocks, seeded RNG)
                                  │
                                  ▼
                          Evaluator suite ──▶ Report API / Postgres
                          (7 built-in scorers)
```

## Packages

- **core** — Pydantic models (`TestCase`, `Trace`, `EvalResult`, `Report`, `RunSummary`) and the YAML-driven gate engine. Pure, dependency-light, used everywhere.
- **runner** — `AgentClient` calls the agent and normalises responses into spans. `MockRegistry` derives deterministic latency, tokens, and tool responses from `seed + test_id`. `run_suite` uses an `asyncio.Semaphore` for parallel, bounded execution.
- **eval** — registry-based scorers. Built-ins: exact match, semantic similarity, JSON schema, tool accuracy F1, latency, token cost, safety (PII + injection), policy compliance. Any function decorated with `@register("name")` becomes a scorer.
- **testgen** — synthesises happy/edge/adversarial cases from OpenAPI operations, prompt placeholders, and JSONL logs. Stable hashes in case IDs keep baselines comparable across runs.
- **cli** — `init`, `generate`, `run`, `gate`, `report`, `replay`, `validate`. Writes `.agentci/report.json` and optionally posts a GitHub commit status.
- **api** — FastAPI store for reports, PR status, replay endpoints, and GitHub webhooks. `ReportStore` is a JSON file today; the interface is the seam for Postgres + pgvector.

## Determinism and replay

Each test ID is hashed with the simulation seed to produce a per-case RNG. Latencies, token counts, and mocked tool outputs are derived from that RNG, so a rerun with the same seed reproduces the same trace. Reports embed full traces; `/runs/{id}/trace/{test_id}` and `agentci replay` reconstruct a seekable timeline.

## Isolation roadmap

The MVP uses `sandbox: inprocess` for portability. The `SimulationConfig.sandbox` field already accepts `gvisor` and `firecracker`; the production adapter boots an isolated microVM, streams the request in, and streams spans out over the same normalised response shape. No evaluator or gate code changes when the sandbox changes.

## Cost as a first-class metric

Every span carries `tokens_in`, `tokens_out`, and `cost_usd`. `RunSummary` aggregates total cost, cost-per-run, and delta versus baseline. `cost_per_run_usd` and `cost_delta_usd` are gateable metrics, so a model upgrade that doubles spend blocks the merge even when accuracy improves.
