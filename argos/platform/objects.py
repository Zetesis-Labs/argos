"""Almacén de artefactos S3 sobre RustFS: escritura en flujo, lectura acotada y URL firmada."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterable, AsyncIterator
from datetime import timedelta
from urllib.parse import urlsplit

import httpx

from argos.core.ports import (
    Clock,
    ObjectMetadata,
    ObjectSizeMismatchError,
    ObjectStoreError,
    ObjectTooLargeError,
    StoredObject,
)
from argos.platform.signing import UNSIGNED_PAYLOAD, presigned_query, signed_headers

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class Sha256Sink:
    def __init__(self) -> None:
        self._digest = hashlib.sha256()

    def update(self, chunk: bytes) -> None:
        self._digest.update(chunk)

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()


async def measured(
    content: AsyncIterable[bytes], *, size: int, sink: Sha256Sink
) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in content:
        total += len(chunk)
        sink.update(chunk)
        yield chunk
    if total != size:
        raise ObjectSizeMismatchError(f"declared {size} bytes but streamed {total}")


class RustFsObjectStore:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret: str,
        region: str,
        clock: Clock,
        timeout: float = 30.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._bucket = bucket
        self._access_key = access_key
        self._secret = secret
        self._region = region
        self._clock = clock
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise ObjectStoreError("the object store is not connected")
        return self._client

    @property
    def _host(self) -> str:
        return urlsplit(self._endpoint).netloc

    def _path(self, key: str | None = None) -> str:
        return f"/{self._bucket}" if key is None else f"/{self._bucket}/{key.lstrip('/')}"

    def _headers(
        self, method: str, path: str, *, extra: dict[str, str] | None = None, payload: str
    ) -> dict[str, str]:
        return signed_headers(
            method=method,
            path=path,
            query={},
            headers={"host": self._host, **(extra or {})},
            payload_hash=payload,
            access_key=self._access_key,
            secret=self._secret,
            region=self._region,
            now=self._clock.now(),
        )

    async def ensure_bucket(self) -> None:
        path = self._path()
        response = await self._http.put(
            f"{self._endpoint}{path}", headers=self._headers("PUT", path, payload=EMPTY_SHA256)
        )
        if response.status_code not in (200, 409):
            raise ObjectStoreError(f"cannot create bucket: {response.status_code}")

    async def put(
        self, key: str, content: AsyncIterable[bytes], *, size: int, mime: str
    ) -> StoredObject:
        path = self._path(key)
        sink = Sha256Sink()
        headers = self._headers(
            "PUT",
            path,
            extra={"content-type": mime, "content-length": str(size)},
            payload=UNSIGNED_PAYLOAD,
        )
        try:
            response = await self._http.put(
                f"{self._endpoint}{path}",
                content=measured(content, size=size, sink=sink),
                headers=headers,
            )
        except httpx.HTTPError as error:
            raise ObjectStoreError(f"cannot write {key}") from error
        if response.status_code != 200:
            raise ObjectStoreError(f"cannot write {key}: {response.status_code}")
        return StoredObject(key=key, sha256=sink.hexdigest, size=size)

    async def stat(self, key: str) -> ObjectMetadata | None:
        path = self._path(key)
        response = await self._http.head(
            f"{self._endpoint}{path}", headers=self._headers("HEAD", path, payload=UNSIGNED_PAYLOAD)
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ObjectStoreError(f"cannot stat {key}: {response.status_code}")
        return ObjectMetadata(
            key=key,
            size=int(response.headers.get("content-length", 0)),
            mime=response.headers.get("content-type", ""),
        )

    async def read(self, key: str, *, limit: int) -> bytes | None:
        path = self._path(key)
        request = self._http.build_request(
            "GET",
            f"{self._endpoint}{path}",
            headers=self._headers("GET", path, payload=UNSIGNED_PAYLOAD),
        )
        response = await self._http.send(request, stream=True)
        try:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise ObjectStoreError(f"cannot read {key}: {response.status_code}")
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) > limit:
                    raise ObjectTooLargeError(f"{key} exceeds {limit} bytes")
            return bytes(buffer)
        finally:
            await response.aclose()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        response = await self._http.delete(
            f"{self._endpoint}{path}",
            headers=self._headers("DELETE", path, payload=UNSIGNED_PAYLOAD),
        )
        if response.status_code not in (200, 204, 404):
            raise ObjectStoreError(f"cannot delete {key}: {response.status_code}")

    def presigned_get(self, key: str, *, expires_in: timedelta) -> str:
        path = self._path(key)
        query = presigned_query(
            method="GET",
            path=path,
            host=self._host,
            access_key=self._access_key,
            secret=self._secret,
            region=self._region,
            now=self._clock.now(),
            expires_in=int(expires_in.total_seconds()),
        )
        return f"{self._endpoint}{path}?{query}"
