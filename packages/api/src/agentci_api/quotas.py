from __future__ import annotations

from fastapi import HTTPException

from agentci_api.auth import OrgContext, get_repository


class QuotaService:
    async def check(self, org: OrgContext, estimated_cost_usd: float = 0.0) -> None:
        if org.monthly_runs + 1 > org.monthly_run_quota:
            raise HTTPException(status_code=429, detail="Monthly run quota exceeded")
        if org.monthly_spend_usd + estimated_cost_usd > org.monthly_budget_usd:
            raise HTTPException(status_code=429, detail="Monthly budget exceeded")

    async def record(self, org_id: str, cost_usd: float) -> None:
        await get_repository().record_usage(org_id, cost_usd)


_quota_service: QuotaService | None = None


def get_quota_service() -> QuotaService:
    global _quota_service
    if _quota_service is None:
        _quota_service = QuotaService()
    return _quota_service
