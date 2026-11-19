from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTCI_", extra="ignore")

    database_url: str = "postgresql+asyncpg://agentci:agentci@localhost:5432/agentci"
    redis_url: str = "redis://localhost:6379/0"
    bootstrap_api_key: str | None = None
    bootstrap_org_name: str = "default"
    monthly_budget_usd: float = 25.0
    monthly_run_quota: int = 10000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    allow_insecure_agent_endpoints: bool = False
    max_agent_concurrency: int = 32
    environment: str = "dev"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
