"""Utilidades de flujo para subir sin cargar el fichero completo en memoria."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator


class UploadTooLargeError(Exception):
    pass


async def peek(content: AsyncIterable[bytes], size: int) -> tuple[bytes, AsyncIterator[bytes]]:
    iterator = content.__aiter__()
    head = bytearray()
    buffered: list[bytes] = []
    while len(head) < size:
        try:
            chunk = await iterator.__anext__()
        except StopAsyncIteration:
            break
        buffered.append(chunk)
        head.extend(chunk)

    async def replay() -> AsyncIterator[bytes]:
        for chunk in buffered:
            yield chunk
        async for chunk in iterator:
            yield chunk

    return bytes(head[:size]), replay()


async def bounded(content: AsyncIterable[bytes], max_bytes: int) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in content:
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(f"upload exceeds {max_bytes} bytes")
        yield chunk
