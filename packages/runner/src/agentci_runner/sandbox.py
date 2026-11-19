from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import docker
import httpx
from pydantic import BaseModel, ConfigDict, Field

from agentci_runner.security import validate_endpoint


class SandboxError(RuntimeError):
    """Raised when a sandbox cannot be created, started or seeded."""


async def _noop_close() -> None:
    return None


class SandboxSpec(BaseModel):
    sandbox: Literal["inprocess", "docker", "gvisor", "firecracker"] = "inprocess"
    image: str | None = None
    runtime: str | None = None
    network_mode: str | None = None
    memory_mb: int = 512
    cpu_quota: int = 50000
    pids_limit: int = 128
    read_only: bool = True
    timeout_seconds: float = 30.0
    container_port: int = 8000
    env_allowlist: list[str] = []
    allow_private_endpoint: bool = False


class SandboxHandle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    endpoint: str
    backend: str
    container_id: str | None = None
    log_reader: Callable[[], str] = Field(default=lambda: "", exclude=True, repr=False)
    closer: Callable[[], Awaitable[None]] = Field(default=_noop_close, exclude=True, repr=False)

    def logs(self) -> str:
        return self.log_reader()

    async def close(self) -> None:
        await self.closer()


def _health_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


class InProcessSandbox:
    def __init__(self, spec: SandboxSpec | None = None) -> None:
        self.spec = spec or SandboxSpec()

    async def start(self, endpoint: str) -> SandboxHandle:
        validated = validate_endpoint(
            endpoint, allow_private=True, allowed_schemes=("http", "https")
        )
        return SandboxHandle(endpoint=validated, backend="inprocess")

    async def close(self) -> None:
        return None


class DockerSandbox:
    def __init__(
        self,
        spec: SandboxSpec,
        *,
        client: Any | None = None,
        poll_interval: float = 0.05,
        health_path: str = "/health",
    ) -> None:
        self.spec = spec
        self.client = client
        self.poll_interval = poll_interval
        self.health_path = health_path

    async def start(self, request_payload: dict[str, Any] | None = None) -> SandboxHandle:
        if not self.spec.image:
            raise SandboxError("docker and gvisor sandboxes require an image")
        container: Any = None
        try:
            container = await asyncio.to_thread(self._run_container, request_payload)
            endpoint = await asyncio.to_thread(self._mapped_endpoint, container)
            await self._wait_until_healthy(endpoint)
        except Exception as exc:
            if container is not None:
                await self._stop_container(container)
            if isinstance(exc, SandboxError):
                raise
            raise SandboxError(f"failed to start sandbox: {exc}") from exc

        def logs() -> str:
            return self._container_logs(container)

        async def close() -> None:
            await self._stop_container(container)

        handle = SandboxHandle(
            endpoint=endpoint,
            backend=self.spec.sandbox,
            container_id=str(container.id),
        )
        handle.log_reader = logs
        handle.closer = close
        return handle

    def _run_container(self, request_payload: dict[str, Any] | None) -> Any:
        client = self.client if self.client is not None else docker.from_env()
        port = f"{self.spec.container_port}/tcp"
        return client.containers.run(
            self.spec.image,
            detach=True,
            ports={port: ("127.0.0.1", None)},
            user="65532:65532",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            read_only=self.spec.read_only,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
            mem_limit=f"{self.spec.memory_mb}m",
            cpu_quota=self.spec.cpu_quota,
            pids_limit=self.spec.pids_limit,
            network_mode=self.spec.network_mode or "bridge",
            runtime=self.spec.runtime or ("runsc" if self.spec.sandbox == "gvisor" else None),
            environment=self._environment(request_payload),
            auto_remove=True,
        )

    def _environment(self, request_payload: dict[str, Any] | None) -> dict[str, str]:
        environment = {key: os.environ[key] for key in self.spec.env_allowlist if key in os.environ}
        if request_payload is not None and "AGENTCI_REQUEST" in self.spec.env_allowlist:
            environment["AGENTCI_REQUEST"] = json.dumps(request_payload, sort_keys=True)
        return environment

    def _mapped_endpoint(self, container: Any) -> str:
        if hasattr(container, "reload"):
            container.reload()
        port = f"{self.spec.container_port}/tcp"
        bindings = (container.ports or {}).get(port) or []
        if not bindings or not bindings[0].get("HostPort"):
            raise SandboxError(f"sandbox container did not publish host port {port}")
        host_port = bindings[0]["HostPort"]
        return validate_endpoint(
            f"http://127.0.0.1:{host_port}",
            allow_private=True,
            allowed_schemes=("http",),
        )

    async def _wait_until_healthy(self, endpoint: str) -> None:
        base = endpoint.rstrip("/")
        path = self.health_path.lstrip("/")
        url = f"{base}/{path}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.spec.timeout_seconds
        client = _health_client(min(2.0, self.spec.timeout_seconds))
        try:
            while True:
                try:
                    response = await client.get(url)
                    if 200 <= response.status_code < 400:
                        return
                except httpx.HTTPError:
                    pass
                if loop.time() >= deadline:
                    raise SandboxError(f"sandbox health check timed out for {url}")
                await asyncio.sleep(self.poll_interval)
        finally:
            await client.aclose()

    @staticmethod
    async def _stop_container(container: Any) -> None:
        for name, kwargs in (("stop", {}), ("remove", {"force": True})):
            try:
                await asyncio.to_thread(getattr(container, name), **kwargs)
            except Exception:
                continue

    @staticmethod
    def _container_logs(container: Any) -> str:
        raw = container.logs()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)


def create_sandbox(spec: SandboxSpec) -> InProcessSandbox | DockerSandbox:
    if spec.sandbox == "inprocess":
        return InProcessSandbox(spec)
    if spec.sandbox in ("docker", "gvisor"):
        return DockerSandbox(spec)
    raise SandboxError(
        "firecracker sandbox requires a host-specific microVM adapter; use docker/gvisor"
    )
