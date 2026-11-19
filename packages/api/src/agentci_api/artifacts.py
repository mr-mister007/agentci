from __future__ import annotations

import contextlib
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Protocol, cast

import boto3

_SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]+")


class ArtifactStore(Protocol):
    async def put(
        self,
        run_id: str,
        name: str,
        data: bytes,
        content_type: str = "application/json",
    ) -> str: ...

    async def get(self, run_id: str, name: str) -> bytes | None: ...

    async def presign(self, run_id: str, name: str, expires_seconds: int = 300) -> str | None: ...


def _validate_part(value: str, label: str) -> str:
    if value in {"", ".", ".."} or _SAFE_NAME.fullmatch(value) is None:
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


class LocalArtifactStore:
    def __init__(self, root: str | Path = ".agentci/artifacts") -> None:
        self.root = Path(root)

    def _path(self, run_id: str, name: str) -> Path:
        return self.root / _validate_part(run_id, "run_id") / _validate_part(name, "name")

    async def put(
        self,
        run_id: str,
        name: str,
        data: bytes,
        content_type: str = "application/json",
    ) -> str:
        path = self._path(run_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                Path(temporary).unlink()
            raise
        return path.resolve().as_uri()

    async def get(self, run_id: str, name: str) -> bytes | None:
        path = self._path(run_id, name)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    async def presign(self, run_id: str, name: str, expires_seconds: int = 300) -> str | None:
        path = self._path(run_id, name)
        return path.resolve().as_uri() if path.exists() else None


def _is_missing(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            return str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}
    return isinstance(exc, KeyError)


class S3ArtifactStore:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        client: Any = None,
    ) -> None:
        self.bucket = bucket
        self._client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        )

    def __repr__(self) -> str:
        return f"S3ArtifactStore(bucket={self.bucket!r})"

    @staticmethod
    def _key(run_id: str, name: str) -> str:
        return f"agentci/{_validate_part(run_id, 'run_id')}/{_validate_part(name, 'name')}"

    async def put(
        self,
        run_id: str,
        name: str,
        data: bytes,
        content_type: str = "application/json",
    ) -> str:
        key = self._key(run_id, name)
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return f"s3://{self.bucket}/{key}"

    async def get(self, run_id: str, name: str) -> bytes | None:
        key = self._key(run_id, name)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise
        return cast("BinaryIO", response["Body"]).read()

    async def presign(self, run_id: str, name: str, expires_seconds: int = 300) -> str | None:
        return cast(
            "str",
            self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": self._key(run_id, name)},
                ExpiresIn=expires_seconds,
            ),
        )


@lru_cache(maxsize=1)
def get_artifact_store() -> ArtifactStore:
    backend = os.environ.get("AGENTCI_ARTIFACT_BACKEND", "local").lower()
    if backend == "local":
        return LocalArtifactStore(os.environ.get("AGENTCI_ARTIFACT_ROOT", ".agentci/artifacts"))
    if backend == "s3":
        bucket = os.environ.get("AGENTCI_S3_BUCKET")
        if not bucket:
            raise RuntimeError("AGENTCI_S3_BUCKET is required for the S3 artifact backend")
        return S3ArtifactStore(
            bucket=bucket,
            endpoint_url=os.environ.get("AGENTCI_S3_ENDPOINT"),
            region=os.environ.get("AGENTCI_S3_REGION", "us-east-1"),
            access_key=os.environ.get("AGENTCI_S3_ACCESS_KEY"),
            secret_key=os.environ.get("AGENTCI_S3_SECRET_KEY"),
        )
    raise ValueError(f"Unsupported artifact backend: {backend!r}")
