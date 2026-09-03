"""Apoyo compartido por los tests: lectura de Langfuse y de INFO FOR DB."""

from __future__ import annotations

import asyncio
import time
from typing import cast

import httpx

from argos.config import Settings
from argos.platform.surreal import JsonValue


def names_in(section: JsonValue | None) -> set[str]:
    return set(section.keys()) if isinstance(section, dict) else set()


def parse_observations(payload: object) -> list[dict[str, JsonValue]]:
    if not isinstance(payload, dict):
        return []
    data = cast(dict[str, object], payload).get("data")
    if not isinstance(data, list):
        return []
    observations: list[dict[str, JsonValue]] = []
    for item in cast(list[object], data):
        if isinstance(item, dict):
            observations.append(cast(dict[str, JsonValue], item))
    return observations


async def wait_for_observations(
    settings: Settings, *, trace_id: str, timeout_seconds: float
) -> list[dict[str, JsonValue]]:
    auth = (settings.langfuse_public_key, settings.langfuse_secret_key.get_secret_value())
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient(timeout=15, auth=auth) as client:
        while time.monotonic() < deadline:
            response = await client.get(
                f"{settings.langfuse_host}/api/public/v2/observations",
                params={"traceId": trace_id, "limit": 100},
            )
            if response.status_code == 200:
                observations = parse_observations(cast(object, response.json()))
                if observations:
                    return observations
            await asyncio.sleep(2)
    return []
