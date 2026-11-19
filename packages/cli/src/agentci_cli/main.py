from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer
from agentci_core.config import DEFAULT_CONFIG, LoadedConfig, load_config
from agentci_core.gates import evaluate_gates
from agentci_core.models import Report, RunSummary, TestCase, TestKind, Trace
from agentci_eval import evaluate, summarize
from agentci_runner import AgentClient, run_suite
from agentci_runner.replay import replay_trace
from agentci_testgen import (
    dump_cases,
    generate_from_config,
    generate_from_llm,
    load_cases,
    parse_cases,
)
from rich.console import Console
from rich.table import Table

from agentci_cli.github import enqueue_run, job_status, post_status, push_report

app = typer.Typer(
    name="agentci",
    help="GitHub Actions for AI agents — test, evaluate, gate, deploy.",
    no_args_is_help=True,
)
console = Console()


def _echo_json(data: object) -> None:
    console.print_json(data=data)


def _load_report(path: Path) -> Report:
    if not path.exists():
        raise typer.BadParameter(f"Report not found: {path}")
    return Report.model_validate_json(path.read_text(encoding="utf-8"))


def _baseline_path(cfg: LoadedConfig) -> Path | None:
    return cfg.resolve(cfg.baseline.path) if cfg.baseline.path else None


def _load_baseline(path: Path | None) -> RunSummary | None:
    if path is None or not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "summary" in raw:
        raw = raw["summary"]
    return RunSummary.model_validate(raw)


def _resolve_cases(config: LoadedConfig, explicit: Path | None) -> list[TestCase]:
    if explicit is not None:
        return load_cases(explicit)
    cases = generate_from_config(config)
    if not cases:
        fallback = config.resolve("tests/cases.yaml")
        if fallback.exists():
            cases = load_cases(fallback)
    return cases


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
) -> None:
    """Create a starter agentci.yaml and sample test suite."""
    config_path = Path("agentci.yaml")
    cases_path = Path("tests/cases.yaml")
    if config_path.exists() and not force:
        raise typer.BadParameter("agentci.yaml already exists (use --force to overwrite)")
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    cases_path.parent.mkdir(parents=True, exist_ok=True)
    sample = [
        TestCase(
            name="greeting returns a helpful answer",
            kind=TestKind.HAPPY,
            input={"message": "Hello, who are you?"},
            expected={"policy": {"forbidden": ["api_key"]}},
            source="manual",
        )
    ]
    if not cases_path.exists() or force:
        cases_path.write_text(dump_cases(sample), encoding="utf-8")
    console.print("[green]✓[/green] Created agentci.yaml and tests/cases.yaml")


@app.command()
def generate(
    source: Path = typer.Option(..., "--from", help="OpenAPI spec, prompt file, or JSONL logs"),
    out: Path = typer.Option(Path("tests/cases.yaml"), "--out", "-o"),
    count: int = typer.Option(50, "--count", "-n"),
    generator: str = typer.Option(
        "auto",
        "--generator",
        help="auto, llm, openapi, prompt, or logs",
    ),
) -> None:
    """Generate synthetic test cases from a spec, prompt, or logs."""
    if not source.exists():
        raise typer.BadParameter(f"Source not found: {source}")
    from agentci_testgen import generate_tests

    if generator == "llm":
        cases = generate_from_llm(source, count)
    elif generator in {"auto", "openapi", "prompt", "logs"}:
        cases = generate_tests(source, count)
    else:
        raise typer.BadParameter(f"Unknown generator: {generator}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dump_cases(cases), encoding="utf-8")
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.kind.value] = counts.get(case.kind.value, 0) + 1
    console.print(
        f"[green]✓[/green] Generated {len(cases)} cases "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(counts.items()))}) → {out}"
    )


@app.command()
def run(
    pr: str | None = typer.Option(None, "--pr", help="Pull request number or branch"),
    baseline: Path | None = typer.Option(None, "--baseline", help="Baseline report JSON"),
    config: Path = typer.Option(Path("agentci.yaml"), "--config", "-c"),
    cases: Path | None = typer.Option(None, "--cases", help="Explicit test case file"),
    out: Path = typer.Option(Path(".agentci/report.json"), "--out", "-o"),
    baseline_out: Path | None = typer.Option(None, "--baseline-out"),
    api_url: str | None = typer.Option(None, "--api-url"),
    api_key: str | None = typer.Option(None, "--api-key"),
    async_run: bool = typer.Option(False, "--async", help="Queue the run on the AgentCI API"),
) -> None:
    """Run the suite, score it, evaluate gates, and write a report."""
    cfg = load_config(config)
    test_cases = _resolve_cases(cfg, cases)
    if not test_cases:
        console.print("[red]No test cases found. Run `agentci generate` first.[/red]")
        raise typer.Exit(2)

    baseline_path = baseline or _baseline_path(cfg)
    base = _load_baseline(baseline_path)
    if async_run:
        payload = {
            "agent": cfg.agent.name,
            "pr": pr,
            "commit": os.environ.get("GITHUB_SHA"),
            "branch": os.environ.get("GITHUB_REF_NAME") or summary_branch(),
            "config_yaml": config.read_text(encoding="utf-8"),
            "cases": [case.model_dump(mode="json") for case in test_cases],
            "baseline_yaml": (
                baseline_path.read_text(encoding="utf-8") if baseline_path is not None else None
            ),
        }
        try:
            job_id = enqueue_run(payload, api_url, api_key)
        except Exception as exc:  # noqa: BLE001 - surfaced as a CLI failure
            raise typer.BadParameter(f"Could not queue run: {exc}") from exc
        console.print(f"[green]✓[/green] Queued {len(test_cases)} cases · job {job_id}")
        return

    console.print(
        f"[bold]AgentCI[/bold] · {cfg.agent.name} · {len(test_cases)} cases · "
        f"seed {cfg.simulation.seed}"
    )

    async def execute() -> list[Trace]:
        async with AgentClient.from_config(cfg.agent, cfg.simulation) as client:
            return await run_suite(
                test_cases,
                client,
                concurrency=cfg.simulation.concurrency,
            )

    traces = asyncio.run(execute())
    results = evaluate(test_cases, traces, cfg.scorers)
    summary = summarize(
        results,
        agent=cfg.agent.name,
        baseline=base,
        pr=pr,
        commit=os.environ.get("GITHUB_SHA"),
        branch=os.environ.get("GITHUB_REF_NAME") or summary_branch(),
    )
    decision = evaluate_gates(
        summary,
        cfg.gates,
        base,
        auto_approve=cfg.baseline.auto_approve,
    )
    report = Report(
        summary=summary,
        gate_passed=decision.passed,
        gates=decision.results,
        auto_approve=decision.auto_approve,
        traces=traces,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    if baseline_out is not None:
        baseline_out.parent.mkdir(parents=True, exist_ok=True)
        baseline_out.write_text(summary.model_dump_json(indent=2), encoding="utf-8")

    _print_report(report, out)
    if check_url := post_status(report, os.environ.get("AGENTCI_REPORT_URL")):
        console.print(f"[dim]GitHub check:[/dim] {check_url}")
    if api := push_report(report, api_url, api_key):
        console.print(f"[dim]Report pushed:[/dim] {api}")

    if not decision.passed:
        raise typer.Exit(1)


def summary_branch() -> str | None:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # noqa: BLE001 - branch metadata is optional
        return None


def _print_report(report: Report, path: Path) -> None:
    summary = report.summary
    table = Table(title="AgentCI Report", show_header=False, title_style="bold")
    table.add_row("Result", "[green]PASS[/green]" if report.gate_passed else "[red]FAIL[/red]")
    table.add_row("Tests", f"{summary.passed}/{summary.total} passed ({summary.pass_rate:.0%})")
    table.add_row(
        "Latency", f"p50 {summary.p50_latency_ms:.0f}ms · p95 {summary.p95_latency_ms:.0f}ms"
    )
    table.add_row(
        "Cost", f"${summary.total_cost_usd:.4f} total · Δ ${summary.cost_delta_usd:+.4f}/run"
    )
    table.add_row(
        "Quality",
        f"tools {summary.tool_accuracy:.0%} · safety {summary.safety_score:.0%} · "
        f"policy {summary.policy_score:.0%}",
    )
    table.add_row("Regressions", str(summary.regressions))
    console.print(table)

    if summary.failed:
        failed = Table(title="Failures")
        failed.add_column("Test")
        failed.add_column("Reason")
        for result in summary.results:
            if not result.passed:
                failed.add_row(result.test_name, result.failure_reason or "unknown")
        console.print(failed)

    gates = Table(title="Gates")
    gates.add_column("Metric")
    gates.add_column("Actual")
    gates.add_column("Threshold")
    gates.add_column("Result")
    for gate in report.gates:
        gates.add_row(
            gate.metric,
            f"{gate.actual:.4f}",
            f"{gate.direction} {gate.threshold:.4f}",
            "[green]pass[/green]" if gate.passed else "[red]fail[/red]",
        )
    console.print(gates)
    console.print(f"[dim]Report: {path}[/dim]")


@app.command()
def gate(report_path: Path = typer.Option(Path(".agentci/report.json"), "--report")) -> None:
    """Re-evaluate gates from an existing report."""
    report = _load_report(report_path)
    cfg = load_config()
    base = _load_baseline(_baseline_path(cfg))
    decision = evaluate_gates(
        report.summary, cfg.gates, base, auto_approve=cfg.baseline.auto_approve
    )
    console.print(
        f"[bold]{decision.status.upper()}[/bold]: {decision.summary} "
        f"(auto-approve: {'yes' if decision.auto_approve else 'no'})"
    )
    if not decision.passed:
        raise typer.Exit(1)


@app.command()
def report(path: Path = typer.Option(Path(".agentci/report.json"), "--report")) -> None:
    """Print a saved report."""
    saved = _load_report(path)
    _print_report(saved, path)


@app.command()
def replay(
    test_id: str = typer.Argument(..., help="Test case id from the report"),
    path: Path = typer.Option(Path(".agentci/report.json"), "--report"),
) -> None:
    """Replay a captured trace as a deterministic timeline."""
    saved = _load_report(path)
    trace = next((item for item in saved.traces if item.test_id == test_id), None)
    if trace is None:
        raise typer.BadParameter(f"No trace for test id {test_id}")
    _echo_json(replay_trace(trace))


@app.command()
def validate(path: Path = typer.Option(Path("tests/cases.yaml"), "--cases")) -> None:
    """Validate a test case file."""
    cases = parse_cases(path.read_text(encoding="utf-8"))
    console.print(f"[green]✓[/green] {len(cases)} valid test cases")


@app.command()
def status(
    job_id: str = typer.Argument(..., help="Job id returned by `agentci run --async`"),
    api_url: str | None = typer.Option(None, "--api-url"),
    api_key: str | None = typer.Option(None, "--api-key"),
) -> None:
    """Check an async run queued on the AgentCI API."""
    data = job_status(job_id, api_url, api_key)
    _echo_json(data)
    if data.get("status") == "failed":
        raise typer.Exit(1)


@app.command()
def worker() -> None:
    """Start the AgentCI queue worker (requires the API package)."""
    from agentci_api.worker import run_worker

    run_worker()


if __name__ == "__main__":
    app()
