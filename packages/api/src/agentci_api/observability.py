from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from agentci_core.models import Report
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SERVICE_NAME = "agentci"
VERSION = "0.1.0"

REQUESTS = Counter(
    "agentci_requests_total",
    "Total HTTP requests.",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "agentci_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "path"],
)
RUNS = Counter(
    "agentci_runs_total",
    "Total evaluation runs.",
    ["result", "gate"],
)
AGENT_CALLS = Counter(
    "agentci_agent_calls_total",
    "Total agent invocations.",
    ["status"],
)
COST = Histogram(
    "agentci_run_cost_usd",
    "Evaluation run cost in USD.",
)
QUEUE_DEPTH = Gauge(
    "agentci_queue_depth",
    "Current queued jobs.",
)


def configure_logging(environment: str = "dev") -> None:
    renderer: Any
    if environment == "prod":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        service=SERVICE_NAME,
        version=VERSION,
        environment=environment,
    )
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        status = 500
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["X-Request-ID"] = request_id
                return response
            finally:
                duration = time.perf_counter() - started
                REQUESTS.labels(
                    method=request.method,
                    path=request.url.path,
                    status=str(status),
                ).inc()
                LATENCY.labels(method=request.method, path=request.url.path).observe(duration)
                get_logger("agentci.http").info(
                    "request_completed",
                    method=request.method,
                    path=request.url.path,
                    status=status,
                    duration_ms=round(duration * 1000, 3),
                )


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def observe_run(report: Report) -> None:
    result = "passed" if report.gate_passed else "failed"
    gate = "auto_approve" if report.auto_approve else "standard"
    RUNS.labels(result=result, gate=gate).inc()
    COST.observe(float(report.summary.total_cost_usd))
