from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from agentci_core.models import Report
from agentci_runner.replay import replay_trace


class ReportStore:
    """JSON-file store. Swap for Postgres + pgvector in multi-tenant deployments."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory or os.environ.get("AGENTCI_DATA_DIR", ".agentci/data"))
        self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def index(self) -> Path:
        return self.directory / "index.json"

    def _read_index(self) -> dict[str, str]:
        if not self.index.exists():
            return {}
        return cast("dict[str, str]", json.loads(self.index.read_text(encoding="utf-8")))

    def save(self, report: Report) -> None:
        path = self.directory / f"{report.summary.run_id}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        index = self._read_index()
        index[report.summary.run_id] = path.name
        self.index.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def get(self, run_id: str) -> Report | None:
        index = self._read_index()
        filename = index.get(run_id)
        if filename is None:
            return None
        path = self.directory / filename
        return (
            Report.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None
        )

    def list(self, pr: str | None = None) -> list[Report]:
        reports = [self.get(run_id) for run_id in self._read_index()]
        result = [report for report in reports if report is not None]
        if pr is not None:
            result = [report for report in result if report.summary.pr == pr]
        return sorted(result, key=lambda report: report.created_at, reverse=True)

    def latest_for_pr(self, pr: str) -> Report | None:
        matches = self.list(pr)
        return matches[0] if matches else None

    def trace(self, run_id: str, test_id: str) -> dict[str, Any] | None:
        report = self.get(run_id)
        if report is None:
            return None
        trace = next((item for item in report.traces if item.test_id == test_id), None)
        return replay_trace(trace) if trace else None
