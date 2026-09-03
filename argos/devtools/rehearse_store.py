"""Ensayo del almacén antes de confiarle producción (S02 §15): escribir, verificar,
leer, borrar y restaurar. Argos no depende de Object Lock como única defensa."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

from argos.config import Settings
from argos.core.ports import S3ObjectStore
from argos.devtools.bootstrap_store import build_store

REHEARSAL = b"ensayo sintetico de Argos: escribir, verificar, leer, borrar y restaurar.\n"


@dataclass(frozen=True)
class Rehearsal:
    key: str
    written: bool
    verified: bool
    read_back: bool
    deleted: bool
    restored: bool

    @property
    def passed(self) -> bool:
        return all((self.written, self.verified, self.read_back, self.deleted, self.restored))


async def once(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def rehearse(store: S3ObjectStore, *, key: str, data: bytes = REHEARSAL) -> Rehearsal:
    digest = hashlib.sha256(data).hexdigest()
    written = await store.put(key, once(data), size=len(data), mime="text/plain")
    metadata = await store.stat(key)
    read_back = await store.read(key, limit=len(data) + 1)
    await store.delete(key)
    missing = await store.stat(key) is None
    restored = await store.put(key, once(data), size=len(data), mime="text/plain")
    legible = await store.read(key, limit=len(data) + 1)
    await store.delete(key)
    return Rehearsal(
        key=key,
        written=written.size == len(data),
        verified=written.sha256 == digest and metadata is not None and metadata.size == len(data),
        read_back=read_back == data,
        deleted=missing,
        restored=restored.sha256 == digest and legible == data,
    )


async def run(settings: Settings) -> Rehearsal:
    store = build_store(settings)
    await store.connect()
    try:
        await store.ensure_bucket()
        return await rehearse(store, key=f"rehearsals/{uuid4().hex}.txt")
    finally:
        await store.close()


def main() -> None:
    report = run_sync()
    sys.stdout.write(f"{report}\n")
    if not report.passed:
        raise SystemExit(1)


def run_sync() -> Rehearsal:
    return asyncio.run(run(Settings()))
