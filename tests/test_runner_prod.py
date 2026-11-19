from __future__ import annotations

from typing import Any

import httpx
import pytest
from agentci_runner import sandbox as sandbox_module
from agentci_runner.retry import (
    RetryableError,
    RetryPolicy,
    is_retryable,
    with_retry,
)
from agentci_runner.sandbox import (
    DockerSandbox,
    InProcessSandbox,
    SandboxError,
    SandboxSpec,
    create_sandbox,
)
from agentci_runner.security import AgentEndpointError, validate_endpoint


def test_endpoint_validation() -> None:
    assert (
        validate_endpoint("HTTPS://API.Example.com:443/v1/invoke?q=1")
        == "https://api.example.com/v1/invoke?q=1"
    )
    with pytest.raises(AgentEndpointError, match="userinfo"):
        validate_endpoint("https://user:secret@api.example.com/invoke")
    with pytest.raises(ValueError, match="fragment"):
        validate_endpoint("https://api.example.com/invoke#section")
    with pytest.raises(ValueError, match="scheme"):
        validate_endpoint("ftp://api.example.com/invoke")
    with pytest.raises(ValueError, match="metadata"):
        validate_endpoint("https://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="metadata"):
        validate_endpoint("http://metadata.google.internal/computeMetadata", allow_private=True)
    with pytest.raises(ValueError, match="private"):
        validate_endpoint("https://10.0.0.5/invoke")
    with pytest.raises(ValueError, match="private"):
        validate_endpoint("https://[::1]/invoke")
    assert validate_endpoint("http://localhost:8000/invoke", allow_private=True) == (
        "http://localhost:8000/invoke"
    )
    assert (
        validate_endpoint("http://LOCALHOST:8000/invoke", allow_private=True)
        == "http://localhost:8000/invoke"
    )
    assert validate_endpoint("http://[::1]:8080/invoke", allow_private=True) == (
        "http://[::1]:8080/invoke"
    )


def test_is_retryable() -> None:
    request = httpx.Request("POST", "https://api.example.com/invoke")
    assert is_retryable(RetryableError("transient"))
    assert is_retryable(TimeoutError("slow"))
    assert is_retryable(httpx.ConnectError("refused"))
    busy = httpx.HTTPStatusError("busy", request=request, response=httpx.Response(429))
    down = httpx.HTTPStatusError("down", request=request, response=httpx.Response(503))
    bad = httpx.HTTPStatusError("bad", request=request, response=httpx.Response(400))
    assert is_retryable(busy)
    assert is_retryable(down)
    assert not is_retryable(ValueError("bad"))
    assert not is_retryable(bad)


async def test_retry_succeeds_on_third_attempt_with_injected_sleep() -> None:
    policy = RetryPolicy(max_attempts=3, initial_backoff_ms=10, jitter=0.0)
    attempts = 0
    sleeps: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("not ready")
        return "ok"

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    assert await with_retry(operation, policy, sleeper) == "ok"
    assert attempts == 3
    assert sleeps == [0.01, 0.02]


async def test_retry_respects_max_attempts_and_skips_value_error() -> None:
    policy = RetryPolicy(max_attempts=2, initial_backoff_ms=10, jitter=0.0)
    attempts = 0
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    async def timeout() -> str:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("slow")

    with pytest.raises(TimeoutError):
        await with_retry(timeout, policy, sleeper)
    assert attempts == 2
    assert sleeps == [0.01]

    attempts = 0
    sleeps.clear()

    async def invalid() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("bad input")

    with pytest.raises(ValueError, match="bad input"):
        await with_retry(invalid, policy, sleeper)
    assert attempts == 1
    assert sleeps == []


async def test_retry_jitter_is_deterministic_per_attempt() -> None:
    policy = RetryPolicy(max_attempts=3, initial_backoff_ms=100, jitter=0.1)
    runs: list[list[float]] = []

    for _ in range(2):
        sleeps: list[float] = []

        async def sleeper(seconds: float, target: list[float] = sleeps) -> None:
            target.append(seconds)

        async def operation() -> str:
            raise RetryableError("again")

        with pytest.raises(RetryableError):
            await with_retry(operation, policy, sleeper)
        runs.append(sleeps)

    assert runs[0] == runs[1]
    assert len(runs[0]) == 2


class FakeContainer:
    def __init__(self) -> None:
        self.id = "container-123"
        self.ports = {"8000/tcp": [{"HostPort": "49153"}]}
        self.stopped = False
        self.removed = False

    def reload(self) -> None:
        return None

    def logs(self) -> bytes:
        return b"ready"

    def stop(self) -> None:
        self.stopped = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container
        self.image: str | None = None
        self.kwargs: dict[str, Any] = {}

    def run(self, image: str, **kwargs: Any) -> FakeContainer:
        self.image = image
        self.kwargs = kwargs
        return self.container


class FakeDockerClient:
    def __init__(self, container: FakeContainer) -> None:
        self.containers = FakeContainers(container)


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class FakeHttpClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.urls: list[str] = []
        self.closed = False

    async def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse(self.status_code)

    async def aclose(self) -> None:
        self.closed = True


def test_create_sandbox_factory() -> None:
    assert isinstance(create_sandbox(SandboxSpec(sandbox="inprocess")), InProcessSandbox)
    assert isinstance(create_sandbox(SandboxSpec(sandbox="docker")), DockerSandbox)
    assert isinstance(create_sandbox(SandboxSpec(sandbox="gvisor")), DockerSandbox)
    with pytest.raises(SandboxError, match="firecracker"):
        create_sandbox(SandboxSpec(sandbox="firecracker"))


async def test_inprocess_sandbox_returns_endpoint_and_noop_close() -> None:
    sandbox = InProcessSandbox()
    endpoint = "http://127.0.0.1:8000/invoke"
    handle = await sandbox.start(endpoint)
    assert handle.endpoint == endpoint
    assert handle.backend == "inprocess"
    assert handle.container_id is None
    assert handle.logs() == ""
    assert handle.model_dump() == {
        "endpoint": endpoint,
        "backend": "inprocess",
        "container_id": None,
    }
    await handle.close()
    await sandbox.close()


async def test_docker_sandbox_security_options_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = FakeContainer()
    client = FakeDockerClient(container)
    http_client = FakeHttpClient()

    def factory(timeout: float) -> FakeHttpClient:
        assert timeout > 0
        return http_client

    monkeypatch.setattr(sandbox_module, "_health_client", factory)
    monkeypatch.setenv("SAFE_VAR", "yes")
    monkeypatch.setenv("SECRET_VAR", "no")

    spec = SandboxSpec(
        sandbox="docker",
        image="agent:test",
        env_allowlist=["SAFE_VAR", "AGENTCI_REQUEST"],
    )
    sandbox = DockerSandbox(spec, client=client, poll_interval=0)
    handle = await sandbox.start({"prompt": "hi"})

    kwargs = client.containers.kwargs
    assert client.containers.image == "agent:test"
    assert kwargs["detach"] is True
    assert kwargs["ports"] == {"8000/tcp": ("127.0.0.1", None)}
    assert kwargs["user"] == "65532:65532"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["read_only"] is True
    assert kwargs["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=64m"}
    assert kwargs["mem_limit"] == "512m"
    assert kwargs["cpu_quota"] == 50000
    assert kwargs["pids_limit"] == 128
    assert kwargs["network_mode"] == "bridge"
    assert kwargs["runtime"] is None
    assert kwargs["auto_remove"] is True
    assert kwargs["environment"] == {"SAFE_VAR": "yes", "AGENTCI_REQUEST": '{"prompt": "hi"}'}
    assert handle.endpoint == "http://127.0.0.1:49153/"
    assert handle.backend == "docker"
    assert handle.container_id == "container-123"
    assert handle.logs() == "ready"
    assert http_client.urls == ["http://127.0.0.1:49153/health"]
    assert http_client.closed is True

    await handle.close()
    assert container.stopped is True
    assert container.removed is True


async def test_gvisor_uses_runsc_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    container = FakeContainer()
    client = FakeDockerClient(container)
    monkeypatch.setattr(
        sandbox_module,
        "_health_client",
        lambda timeout: FakeHttpClient(),
    )
    sandbox = DockerSandbox(
        SandboxSpec(sandbox="gvisor", image="agent:test"),
        client=client,
        poll_interval=0,
    )
    await sandbox.start()
    assert client.containers.kwargs["runtime"] == "runsc"


async def test_docker_health_failure_stops_container(monkeypatch: pytest.MonkeyPatch) -> None:
    container = FakeContainer()
    client = FakeDockerClient(container)
    monkeypatch.setattr(
        sandbox_module,
        "_health_client",
        lambda timeout: FakeHttpClient(status_code=503),
    )
    sandbox = DockerSandbox(
        SandboxSpec(sandbox="docker", image="agent:test", timeout_seconds=0.01),
        client=client,
        poll_interval=0,
    )
    with pytest.raises(SandboxError, match="health"):
        await sandbox.start()
    assert container.stopped is True
