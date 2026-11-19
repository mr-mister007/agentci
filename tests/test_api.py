from __future__ import annotations

from agentci_api.store import ReportStore
from agentci_core.models import Report, RunSummary


def _report(passed: bool, pr: str = "12") -> Report:
    return Report(
        summary=RunSummary(
            agent="demo",
            pr=pr,
            total=2,
            passed=2 if passed else 1,
            pass_rate=1.0 if passed else 0.5,
        ),
        gate_passed=passed,
        auto_approve=passed,
    )


def test_store_roundtrip_and_pr_filter(tmp_path: object) -> None:
    store = ReportStore(tmp_path)  # type: ignore[arg-type]
    good = _report(True)
    bad = _report(False, pr="13")
    store.save(good)
    store.save(bad)
    assert store.get(good.summary.run_id) is not None
    assert len(store.list()) == 2
    assert len(store.list(pr="12")) == 1
    assert store.latest_for_pr("13") is not None
    assert store.trace(good.summary.run_id, "missing") is None


def test_api_endpoints(tmp_path: object) -> None:
    import agentci_api.main as api_main
    from fastapi.testclient import TestClient

    store = ReportStore(tmp_path)  # type: ignore[arg-type]
    api_main.store = store
    app = api_main.app
    report = _report(True)
    store.save(report)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/runs").json()[0]["run_id"] == report.summary.run_id
        assert client.get(f"/runs/{report.summary.run_id}").status_code == 200
        assert client.get("/pr/12/status").json()["state"] == "success"
        assert client.get("/runs/missing").status_code == 404
        assert client.post("/webhooks/github", json={"number": 12}).json()["state"] == "success"
