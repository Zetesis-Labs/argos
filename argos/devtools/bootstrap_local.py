"""Prepara toda la plataforma local de forma idempotente."""

from __future__ import annotations

import asyncio
import sys

from argos.config import Settings
from argos.core.policy import Policy
from argos.devtools.bootstrap_bus import declare_topology
from argos.devtools.bootstrap_db import apply_schema
from argos.devtools.bootstrap_store import ensure_bucket
from argos.devtools.project_knowledge import project_knowledge


async def prepare_local(settings: Settings, policy: Policy) -> None:
    await apply_schema(settings)
    await declare_topology(settings, policy)
    await ensure_bucket(settings)
    await project_knowledge(settings)


def main() -> None:
    asyncio.run(prepare_local(Settings(), Policy()))
    sys.stdout.write("plataforma local preparada\n")
