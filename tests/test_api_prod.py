from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from agentci_api.auth import require_admin, require_org, set_repository_for_tests
from agentci_api.db import Database, set_database_for_tests
from agentci_api.models_db import ApiKey
from agentci_api.quotas import get_quota_service
from agentci_api.repository import ReportRepository
from agentci_api.settings import reload_settings
from agentci_core.models import Report, RunSummary
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.pool import StaticPool


def _report(pr: str = "12", agent: str = "demo") -> Report:
    return Report(
        summary=RunSummary(agent=agent, pr=pr, total=2, passed=2, pass_rate=1.0),
        gate_passed=True,
        auto_approve=True,
    )


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = Database(
        "sqlite+aiosqlite:///:memory:",
        engine_kwargs={"poolclass": StaticPool, "connect_args": {"check_same_thread": False}},
    )
    await database.init()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def repo(db: Database) -> AsyncIterator[ReportRepository]:
    repository = ReportRepository(db)
    set_database_for_tests(db)
    set_repository_for_tests(repository)
    try:
        yield repository
    finally:
        set_repository_for_tests(None)
        set_database_for_tests(None)


async def test_org_creation_and_api_key_resolution(repo: ReportRepository) -> None:
    org = await repo.create_org("Acme", "acme", 50.0, 5)
    assert org.slug == "acme"
    assert org.monthly_budget_usd == 50.0
    assert await repo.get_org(org.id) == org
    await repo.db.health()

    plaintext, prefix = await repo.create_api_key(org.id, "ci")
    assert plaintext.startswith("ac_live_")
    assert len(plaintext) == 40
    assert plaintext.startswith(prefix)
    assert await repo.resolve_api_key(plaintext) == org
    assert await repo.resolve_api_key("ac_live_invalid") is None

    async with repo.db.session() as session:
        key = (await session.execute(select(ApiKey).where(ApiKey.prefix == prefix))).scalar_one()
        assert key.key_hash == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        assert key.last_used_at is not None
        key.revoked_at = datetime.now(UTC)
        await session.commit()
    assert await repo.resolve_api_key(plaintext) is None


async def test_reports_and_baselines(repo: ReportRepository) -> None:
    org = await repo.create_org("Acme", "acme")
    other = await repo.create_org("Other", "other")
    good = _report("12")
    bad = _report("13")
    bad.gate_passed = False
    await repo.save_report(good, org.id)
    await repo.save_report(bad, org.id)

    assert await repo.get_report(org.id, good.summary.run_id) == good
    assert await repo.get_report(other.id, good.summary.run_id) is None
    assert len(await repo.list_reports(org.id)) == 2
    assert len(await repo.list_reports(org.id, pr="12")) == 1
    assert await repo.latest_for_pr(org.id, "12") == good
    assert await repo.latest_for_pr(org.id, "99") is None

    assert await repo.get_baseline(org.id, "demo") is None
    await repo.save_baseline(org.id, "demo", good)
    assert await repo.get_baseline(org.id, "demo") == good
    assert await repo.get_baseline(other.id, "demo") is None
    replacement = _report("12")
    await repo.save_baseline(org.id, "demo", replacement)
    baseline = await repo.get_baseline(org.id, "demo")
    assert baseline is not None
    assert baseline.summary.run_id == replacement.summary.run_id


async def test_quota_rejection(repo: ReportRepository) -> None:
    org = await repo.create_org("Acme", "acme", 1.0, 1)
    quota = get_quota_service()
    await quota.check(org)
    await quota.record(org.id, 1.0)
    assert await repo.monthly_usage(org.id) == (1.0, 1)

    refreshed = await repo.get_org(org.id)
    assert refreshed is not None
    with pytest.raises(HTTPException) as quota_error:
        await quota.check(refreshed)
    assert quota_error.value.status_code == 429

    budget_org = await repo.create_org("Budget", "budget", 1.0, 100)
    await quota.record(budget_org.id, 1.0)
    refreshed_budget = await repo.get_org(budget_org.id)
    assert refreshed_budget is not None
    with pytest.raises(HTTPException) as budget_error:
        await quota.check(refreshed_budget, estimated_cost_usd=0.01)
    assert budget_error.value.status_code == 429


async def test_plaintext_keys_never_stored(repo: ReportRepository) -> None:
    plaintext = "ac_live_supersecretbootstraptoken"
    org = await repo.create_org("Acme", "acme")
    prefix = await repo.import_api_key(org.id, "bootstrap", plaintext)
    assert prefix == plaintext[:16]
    assert await repo.import_api_key(org.id, "bootstrap", plaintext) == prefix
    assert await repo.resolve_api_key(plaintext) == org

    async with repo.db.session() as session:
        rows = (await session.execute(text("SELECT * FROM api_keys"))).mappings().all()
    assert len(rows) == 1
    row = rows[0]
    assert row["key_hash"] == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert row["prefix"] != plaintext
    assert plaintext not in "".join(str(value) for value in row.values())


async def test_auth_dependencies(repo: ReportRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    org = await repo.create_org("Acme", "acme")
    plaintext, _ = await repo.create_api_key(org.id, "ci")
    assert await require_org(x_api_key=plaintext) == org
    assert await require_org(authorization=f"Bearer {plaintext}") == org
    with pytest.raises(HTTPException) as missing:
        await require_org()
    assert missing.value.status_code == 401

    monkeypatch.setenv("AGENTCI_BOOTSTRAP_API_KEY", "admin-secret")
    try:
        reload_settings()
        await require_admin(x_admin_token="admin-secret")
        with pytest.raises(HTTPException) as invalid:
            await require_admin(x_admin_token="wrong")
        assert invalid.value.status_code == 401
    finally:
        monkeypatch.delenv("AGENTCI_BOOTSTRAP_API_KEY", raising=False)
        reload_settings()
