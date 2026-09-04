"""Prepara toda la plataforma local de forma idempotente."""

from __future__ import annotations

import asyncio
import sys

from argos.config import Settings
from argos.devtools.bootstrap_db import apply_schema
from argos.devtools.project_knowledge import project_knowledge


async def prepare_local(settings: Settings) -> None:
    await apply_schema(settings)
    await project_knowledge(settings)


def main() -> None:
    asyncio.run(prepare_local(Settings()))
    sys.stdout.write("plataforma local preparada\n")
