from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from functools import lru_cache
from typing import Any

_TEST_LIMITER: RateLimiter | None = None


class RateLimiter:
    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url
        self._redis: Any = None
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Any:
        if self._redis is None:
            import redis.asyncio as redis_asyncio

            module: Any = redis_asyncio
            self._redis = module.from_url(self.redis_url or "redis://localhost:6379/0")
        return self._redis

    async def check_and_increment(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        if limit <= 0:
            return False, 0
        if self.redis_url:
            return await self._redis_check(key, limit, window_seconds)
        return await self._memory_check(key, limit, window_seconds)

    async def _memory_check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False, 0
            events.append(now)
            return True, limit - len(events)

    async def _redis_check(
        self,
        key: str,
        limit: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        client = await self._get_redis()
        redis_key = f"agentci:ratelimit:{key}"
        pipeline = client.pipeline()
        try:
            await pipeline.incr(redis_key)
            await pipeline.expire(redis_key, window_seconds, nx=True)
            await pipeline.ttl(redis_key)
            results = await pipeline.execute()
        finally:
            await pipeline.aclose()
        count = int(results[0])
        return count <= limit, max(0, limit - count)


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    if _TEST_LIMITER is not None:
        return _TEST_LIMITER
    return RateLimiter(os.environ.get("AGENTCI_REDIS_URL") or None)


def set_rate_limiter_for_tests(limiter: RateLimiter | None) -> None:
    global _TEST_LIMITER
    _TEST_LIMITER = limiter
    get_rate_limiter.cache_clear()
