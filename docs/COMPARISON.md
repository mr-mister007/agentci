# Why AgentCI

| | AgentCI | LangSmith | Braintrust | Manual QA |
| --- | --- | --- | --- | --- |
| Simulation-first test generation | ✓ | — | partial | — |
| Deterministic seeded replays | ✓ | — | — | — |
| Hard merge gates (block the PR) | ✓ | — | — | — |
| Token cost as a gateable metric | ✓ | reports only | reports only | — |
| OpenAPI / prompt / log ingestion | ✓ | — | partial | — |
| GitHub status checks out of the box | ✓ | — | — | — |
| Observability / tracing | basic | ✓ | ✓ | — |

## Case study (illustrative demo data)

A support agent team added AgentCI to their PR pipeline. Generated tests from their OpenAPI spec
covered order lookup, refunds, and prompt-injection variants. On the first run, 47% of previously
unmeasured regressions were caught before deploy: a refund tool rename broke tool accuracy, and an
upgraded model leaked PII in 3 adversarial cases. Both were blocked by gates, not discovered in
production.

The baseline report is committed as `.agentci/baseline.json`; every PR compares against it and
fails on any regression, latency spike, or cost increase above threshold.
