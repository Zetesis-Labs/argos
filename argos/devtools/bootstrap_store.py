"""Crea el bucket privado de artefactos de forma idempotente (S02 §10)."""

from __future__ import annotations

import asyncio
import sys

from argos.config import Settings
from argos.core.ports import Clock
from argos.platform.clock import SystemClock
from argos.platform.objects import RustFsObjectStore


def build_store(settings: Settings, *, clock: Clock | None = None) -> RustFsObjectStore:
    return RustFsObjectStore(
        endpoint=settings.artifact_endpoint,
        bucket=settings.artifact_bucket,
        access_key=settings.artifact_access_key,
        secret=settings.artifact_secret_key.get_secret_value(),
        region=settings.artifact_region,
        clock=clock or SystemClock(),
    )


async def ensure_bucket(settings: Settings) -> None:
    store = build_store(settings)
    await store.connect()
    try:
        await store.ensure_bucket()
    finally:
        await store.close()


def main() -> None:
    settings = Settings()
    asyncio.run(ensure_bucket(settings))
    sys.stdout.write(f"bucket {settings.artifact_bucket} ready at {settings.artifact_endpoint}\n")
