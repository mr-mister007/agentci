from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx
from pydantic import BaseModel, Field


class RetryableError(RuntimeError):
    """Raised by callers to mark a transient operation failure."""


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=3, ge=1)
    initial_backoff_ms: int = Field(default=100, ge=0)
    max_backoff_ms: int = Field(default=2000, ge=0)
    backoff_multiplier: float = Field(default=2.0, gt=0)
    jitter: float = Field(default=0.1, ge=0, le=1)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, RetryableError):
        return True
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.NetworkError,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return False


async def with_retry[T](
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """Run an async operation with bounded exponential backoff."""
    effective = policy or RetryPolicy()
    sleeper = sleep or asyncio.sleep
    for attempt in range(effective.max_attempts):
        try:
            return await operation()
        except Exception as exc:
            if not is_retryable(exc) or attempt == effective.max_attempts - 1:
                raise
            backoff_ms = min(
                effective.initial_backoff_ms * effective.backoff_multiplier**attempt,
                effective.max_backoff_ms,
            )
            backoff = backoff_ms / 1000
            if effective.jitter > 0:
                offset = random.Random(attempt).uniform(-effective.jitter, effective.jitter)
                backoff *= 1 + offset
            await sleeper(max(0.0, backoff))
    raise RuntimeError("retry policy exhausted")
