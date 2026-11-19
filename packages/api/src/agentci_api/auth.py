from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Annotated

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict

from agentci_api.settings import get_settings

if TYPE_CHECKING:
    from agentci_api.repository import ReportRepository


class OrgContext(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    monthly_budget_usd: float
    monthly_run_quota: int
    monthly_spend_usd: float = 0.0
    monthly_runs: int = 0


_repository: ReportRepository | None = None


def get_repository() -> ReportRepository:
    global _repository
    if _repository is None:
        from agentci_api.repository import ReportRepository

        _repository = ReportRepository()
    return _repository


def set_repository_for_tests(repo: ReportRepository | None) -> None:
    global _repository
    _repository = repo


async def require_org(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> OrgContext:
    token = x_api_key.strip() if x_api_key else None
    if not token and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer":
            token = value.strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    org = await get_repository().resolve_api_key(token)
    if org is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return org


async def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    expected = get_settings().bootstrap_api_key
    token = x_admin_token.strip() if x_admin_token else None
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
