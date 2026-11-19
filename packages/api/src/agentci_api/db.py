from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from agentci_api.models_db import Base
from agentci_api.settings import get_settings


class Database:
    def __init__(self, url: str, *, engine_kwargs: dict[str, Any] | None = None) -> None:
        self.url = url
        self.engine_kwargs = dict(engine_kwargs or {})
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    async def init(self) -> None:
        if self._engine is not None:
            return
        kwargs = dict(self.engine_kwargs)
        if ":memory:" in self.url:
            kwargs.setdefault("poolclass", StaticPool)
            kwargs.setdefault("connect_args", {"check_same_thread": False})
        self._engine = create_async_engine(self.url, pool_pre_ping=True, **kwargs)
        self._sessionmaker = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )
        if self._should_create_tables():
            async with self._engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._sessionmaker is None:
            raise RuntimeError("Database.init() must be called before session()")
        async with self._sessionmaker() as session:
            yield session

    async def health(self) -> None:
        async with self._require_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))

    def _require_engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database.init() must be called before health()")
        return self._engine

    def _should_create_tables(self) -> bool:
        if ":memory:" in self.url:
            return True
        environment = get_settings().environment.lower()
        return environment not in {"prod", "production"}


_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database(get_settings().database_url)
    return _database


def set_database_for_tests(db: Database | None) -> None:
    global _database
    _database = db
