from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from agentci_core.models import Report
from sqlalchemy import select, update

from agentci_api.auth import OrgContext
from agentci_api.db import Database, get_database
from agentci_api.models_db import ApiKey, Baseline, Org, Run
from agentci_api.settings import get_settings

KEY_PREFIX = "ac_live_"
KEY_SECRET_CHARS = 32


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _new_key() -> str:
    secret = secrets.token_urlsafe(KEY_SECRET_CHARS * 3 // 4)
    return f"{KEY_PREFIX}{secret[:KEY_SECRET_CHARS]}"


def _prefix(plaintext: str) -> str:
    return plaintext[: len(KEY_PREFIX) + 8]


def _org_context(org: Org) -> OrgContext:
    return OrgContext(
        id=org.id,
        name=org.name,
        slug=org.slug,
        monthly_budget_usd=org.monthly_budget_usd,
        monthly_run_quota=org.monthly_run_quota,
        monthly_spend_usd=org.monthly_spend_usd,
        monthly_runs=org.monthly_runs,
    )


class ReportRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db if db is not None else get_database()

    async def create_org(
        self,
        name: str,
        slug: str,
        monthly_budget_usd: float | None = None,
        monthly_run_quota: int | None = None,
    ) -> OrgContext:
        settings = get_settings()
        org = Org(
            id=str(uuid.uuid4()),
            name=name,
            slug=slug,
            monthly_budget_usd=(
                settings.monthly_budget_usd if monthly_budget_usd is None else monthly_budget_usd
            ),
            monthly_run_quota=(
                settings.monthly_run_quota if monthly_run_quota is None else monthly_run_quota
            ),
        )
        async with self.db.session() as session:
            session.add(org)
            await session.commit()
        return _org_context(org)

    async def get_org(self, org_id: str) -> OrgContext | None:
        async with self.db.session() as session:
            org = await session.get(Org, org_id)
        return _org_context(org) if org is not None else None

    async def create_api_key(self, org_id: str, name: str) -> tuple[str, str]:
        plaintext = _new_key()
        prefix = _prefix(plaintext)
        async with self.db.session() as session:
            session.add(
                ApiKey(
                    id=str(uuid.uuid4()),
                    org_id=org_id,
                    name=name,
                    prefix=prefix,
                    key_hash=_hash_key(plaintext),
                )
            )
            await session.commit()
        return plaintext, prefix

    async def import_api_key(self, org_id: str, name: str, plaintext: str) -> str:
        key_hash = _hash_key(plaintext)
        prefix = _prefix(plaintext)
        async with self.db.session() as session:
            existing = await session.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))
            if existing is not None:
                return existing.prefix
            session.add(
                ApiKey(
                    id=str(uuid.uuid4()),
                    org_id=org_id,
                    name=name,
                    prefix=prefix,
                    key_hash=key_hash,
                )
            )
            await session.commit()
        return prefix

    async def resolve_api_key(self, plaintext: str) -> OrgContext | None:
        if not plaintext:
            return None
        key_hash = _hash_key(plaintext)
        async with self.db.session() as session:
            result = await session.execute(
                select(ApiKey, Org)
                .join(Org, Org.id == ApiKey.org_id)
                .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
            )
            row = result.first()
            if row is None:
                return None
            api_key = row[0]
            org = row[1]
            api_key.last_used_at = datetime.now(UTC)
            await session.commit()
            return _org_context(org)

    async def save_report(self, report: Report, org_id: str) -> None:
        summary = report.summary
        async with self.db.session() as session:
            run = await session.get(Run, summary.run_id)
            if run is not None and run.org_id != org_id:
                raise ValueError("run_id belongs to a different organization")
            if run is None:
                run = Run(run_id=summary.run_id, org_id=org_id)
                session.add(run)
            run.agent = summary.agent
            run.pr = summary.pr
            run.commit_sha = summary.commit
            run.branch = summary.branch
            run.gate_passed = report.gate_passed
            run.auto_approve = report.auto_approve
            run.cost_usd = summary.total_cost_usd
            run.created_at = report.created_at
            run.report = report.model_dump(mode="json")
            await session.commit()

    async def get_report(self, org_id: str, run_id: str) -> Report | None:
        async with self.db.session() as session:
            payload = await session.scalar(
                select(Run.report).where(Run.org_id == org_id, Run.run_id == run_id)
            )
        return Report.model_validate(payload) if payload is not None else None

    async def list_reports(self, org_id: str, pr: str | None = None) -> list[Report]:
        statement = select(Run.report).where(Run.org_id == org_id)
        if pr is not None:
            statement = statement.where(Run.pr == pr)
        statement = statement.order_by(Run.created_at.desc())
        async with self.db.session() as session:
            rows = (await session.scalars(statement)).all()
        return [Report.model_validate(row) for row in rows]

    async def latest_for_pr(self, org_id: str, pr: str) -> Report | None:
        async with self.db.session() as session:
            payload = await session.scalar(
                select(Run.report)
                .where(Run.org_id == org_id, Run.pr == pr)
                .order_by(Run.created_at.desc())
                .limit(1)
            )
        return Report.model_validate(payload) if payload is not None else None

    async def save_baseline(self, org_id: str, agent: str, report: Report) -> None:
        async with self.db.session() as session:
            baseline = await session.scalar(
                select(Baseline).where(Baseline.org_id == org_id, Baseline.agent == agent)
            )
            if baseline is None:
                baseline = Baseline(id=str(uuid.uuid4()), org_id=org_id, agent=agent)
                session.add(baseline)
            baseline.run_id = report.summary.run_id
            baseline.report = report.model_dump(mode="json")
            baseline.created_at = report.created_at
            await session.commit()

    async def get_baseline(self, org_id: str, agent: str) -> Report | None:
        async with self.db.session() as session:
            payload = await session.scalar(
                select(Baseline.report).where(Baseline.org_id == org_id, Baseline.agent == agent)
            )
        return Report.model_validate(payload) if payload is not None else None

    async def record_usage(self, org_id: str, cost_usd: float, runs: int = 1) -> None:
        async with self.db.session() as session:
            await session.execute(
                update(Org)
                .where(Org.id == org_id)
                .values(
                    monthly_spend_usd=Org.monthly_spend_usd + cost_usd,
                    monthly_runs=Org.monthly_runs + runs,
                )
            )
            await session.commit()

    async def monthly_usage(self, org_id: str) -> tuple[float, int]:
        async with self.db.session() as session:
            row = (
                await session.execute(
                    select(Org.monthly_spend_usd, Org.monthly_runs).where(Org.id == org_id)
                )
            ).first()
        if row is None:
            return (0.0, 0)
        return (float(row[0]), int(row[1]))
