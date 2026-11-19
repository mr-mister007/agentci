from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from agentci_api.artifacts import (
    ArtifactStore,
    LocalArtifactStore,
    S3ArtifactStore,
    get_artifact_store,
)
from agentci_api.observability import (
    RequestIDMiddleware,
    configure_logging,
    metrics_response,
    observe_run,
)
from agentci_api.rate_limit import RateLimiter, get_rate_limiter, set_rate_limiter_for_tests
from agentci_core.models import Report, RunSummary
from agentci_core.models import TestCase as CoreTestCase
from agentci_testgen.llm import (
    LLMConfig,
    LLMGenerationError,
    LLMTestGenerator,
    generate_with_fallback,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient


async def test_local_artifact_store_roundtrip_and_sanitisation(tmp_path: Path) -> None:
    store: ArtifactStore = LocalArtifactStore(tmp_path)
    payload = b'{"cases": 1}'
    uri = await store.put("run_1", "cases.json", payload)
    assert uri.startswith("file://")
    assert await store.get("run_1", "cases.json") == payload
    assert await store.presign("run_1", "cases.json") == uri
    assert await store.get("run_1", "missing.json") is None
    assert await store.presign("run_1", "missing.json") is None

    escaped = f"pwned-{uuid.uuid4().hex}"
    invalid_pairs = (
        (f"../{escaped}", "x.json"),
        ("run_1", f"../{escaped}"),
        ("/absolute", "x.json"),
        ("run_1", "/absolute.json"),
        ("..", "x.json"),
        ("run_1", "."),
    )
    for run_id, name in invalid_pairs:
        with pytest.raises(ValueError):
            await store.put(run_id, name, b"x")
    assert not (tmp_path.parent / escaped).exists()


def test_get_artifact_store_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENTCI_ARTIFACT_BACKEND", raising=False)
    monkeypatch.setenv("AGENTCI_ARTIFACT_ROOT", str(tmp_path))
    get_artifact_store.cache_clear()
    try:
        store = get_artifact_store()
        assert isinstance(store, LocalArtifactStore)
        assert store.root == tmp_path
        assert get_artifact_store() is store
    finally:
        get_artifact_store.cache_clear()


class _FakeClientError(Exception):
    def __init__(self) -> None:
        self.response: dict[str, Any] = {"Error": {"Code": "NoSuchKey"}}
        super().__init__("missing")


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.presign_calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> None:
        self.put_calls.append(kwargs)
        self.objects[str(kwargs["Key"])] = bytes(kwargs["Body"])

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        key = str(kwargs["Key"])
        if key not in self.objects:
            raise _FakeClientError()
        return {"Body": _FakeBody(self.objects[key])}

    def generate_presigned_url(
        self,
        operation: str,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str:
        self.presign_calls.append(
            {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn}
        )
        return f"https://s3.example/{Params['Key']}?expires={ExpiresIn}"


async def test_s3_artifact_store_put_get_presign() -> None:
    fake = _FakeS3Client()
    store = S3ArtifactStore(
        "bucket",
        access_key="not-in-repr",
        secret_key="secret-not-in-repr",
        client=fake,
    )
    uri = await store.put("run-1", "report.json", b"{}")
    assert uri == "s3://bucket/agentci/run-1/report.json"
    assert fake.put_calls[0]["ContentType"] == "application/json"
    assert await store.get("run-1", "report.json") == b"{}"
    assert await store.get("run-1", "missing.json") is None
    url = await store.presign("run-1", "report.json", expires_seconds=42)
    assert url == "https://s3.example/agentci/run-1/report.json?expires=42"
    assert fake.presign_calls[0]["operation"] == "get_object"
    assert "not-in-repr" not in repr(store)
    assert "secret-not-in-repr" not in repr(store)


class _FakeLLMResponse:
    def __init__(self, content: str, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self.content}}]}


class _FakeLLMClient:
    def __init__(self, content: str = "", error: Exception | None = None) -> None:
        self.response = _FakeLLMResponse(content)
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _FakeLLMResponse:
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


async def test_llm_generation_fenced_json_and_stable_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test-secret")
    items = [
        {
            "name": "happy path",
            "kind": "happy",
            "input": {"q": "hi"},
            "expected": {"status": 200},
            "tags": ["core"],
        },
        {
            "name": "empty input",
            "kind": "edge",
            "input": {"q": ""},
            "expected": {},
            "tags": [],
        },
    ]
    fake = _FakeLLMClient("```json\n" + json.dumps(items) + "\n```")
    config = LLMConfig(
        api_key_env="TEST_LLM_KEY",
        base_url="https://llm.example/v1/",
        model="test-model",
    )
    generator = LLMTestGenerator(config, client=fake)
    source = "Summarize {{ticket}}"
    cases = await generator.generate(source, "prompt", count=2)
    assert [case.kind.value for case in cases] == ["happy", "edge"]
    assert all(case.id.startswith("llm-") for case in cases)

    repeated = await generator.generate(source, "prompt", count=2)
    assert [case.id for case in repeated] == [case.id for case in cases]
    assert fake.calls[0]["url"] == "https://llm.example/v1/chat/completions"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer sk-test-secret"
    assert fake.calls[0]["json"]["model"] == "test-model"
    system_prompt = fake.calls[0]["json"]["messages"][0]["content"]
    assert "JSON array" in system_prompt
    assert "adversarial" in system_prompt


async def test_llm_parses_raw_json_and_rejects_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "key")
    config = LLMConfig(api_key_env="TEST_LLM_KEY")
    valid = _FakeLLMClient(json.dumps([{"name": "raw", "kind": "happy", "input": {"q": "hi"}}]))
    cases = await LLMTestGenerator(config, client=valid).generate("source", "logs", count=1)
    assert len(cases) == 1
    assert cases[0].name == "raw"

    invalid = _FakeLLMClient("not json")
    with pytest.raises(LLMGenerationError):
        await LLMTestGenerator(config, client=invalid).generate("source", "logs", count=1)


async def test_llm_http_error_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "key")
    config = LLMConfig(api_key_env="TEST_LLM_KEY")
    fake = _FakeLLMClient(error=httpx.ConnectError("boom"))
    with pytest.raises(LLMGenerationError):
        await LLMTestGenerator(config, client=fake).generate("source", "openapi", count=1)

    monkeypatch.delenv("TEST_LLM_FALLBACK_KEY", raising=False)
    fallback_config = LLMConfig(api_key_env="TEST_LLM_FALLBACK_KEY")
    fallback = [CoreTestCase(name="fallback", input={})]
    assert await generate_with_fallback("source", "logs", 5, fallback_config, fallback) == fallback
    assert await generate_with_fallback("source", "logs", 5, fallback_config) == []


def test_request_id_middleware_and_metrics() -> None:
    async def endpoint(request: Request) -> Response:
        return JSONResponse({"request_id": request.state.request_id})

    app = Starlette(routes=[Route("/ping", endpoint)])
    app.add_middleware(RequestIDMiddleware)
    client = TestClient(app)

    response = client.get("/ping", headers={"X-Request-ID": "req-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-123"
    assert response.json()["request_id"] == "req-123"

    generated = client.get("/ping")
    assert generated.headers["X-Request-ID"]
    assert generated.headers["X-Request-ID"] != "req-123"

    configure_logging("dev")
    report = Report(
        summary=RunSummary(agent="demo", total=1, passed=1, pass_rate=1.0, total_cost_usd=0.5),
        gate_passed=True,
    )
    observe_run(report)
    metrics = metrics_response()
    assert metrics.status_code == 200
    assert b"agentci_requests_total" in metrics.body
    assert b"agentci_runs_total" in metrics.body


async def test_in_memory_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTCI_REDIS_URL", raising=False)
    limiter = RateLimiter()
    assert await limiter.check_and_increment("org:1", 2) == (True, 1)
    assert await limiter.check_and_increment("org:1", 2) == (True, 0)
    assert await limiter.check_and_increment("org:1", 2) == (False, 0)
    assert await limiter.check_and_increment("org:2", 1) == (True, 0)
    assert await limiter.check_and_increment("org:3", 0) == (False, 0)

    set_rate_limiter_for_tests(limiter)
    try:
        assert get_rate_limiter() is limiter
    finally:
        set_rate_limiter_for_tests(None)
