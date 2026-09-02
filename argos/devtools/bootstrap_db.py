"""Aplica db/schema.surql de forma idempotente (constitución §7)."""

from __future__ import annotations

import asyncio
import sys

from argos.config import Settings
from argos.platform.surreal import SurrealHttp

PLACEHOLDERS = ("{{AGENT_PASSWORD}}", "{{RUNTIME_PASSWORD}}")


def render_schema(template: str, *, agent_password: str, runtime_password: str) -> str:
    for password in (agent_password, runtime_password):
        if "'" in password or "\\" in password:
            raise ValueError("Las contraseñas de SurrealDB no pueden contener comillas ni barras")
    return template.replace(PLACEHOLDERS[0], agent_password).replace(
        PLACEHOLDERS[1], runtime_password
    )


async def apply_schema(settings: Settings) -> None:
    template = settings.schema_path.read_text(encoding="utf-8")
    rendered = render_schema(
        template,
        agent_password=settings.surreal_agent_password.get_secret_value(),
        runtime_password=settings.surreal_runtime_password.get_secret_value(),
    )
    await SurrealHttp(settings.surreal_url).sql(rendered, auth=settings.root_auth)


def main() -> None:
    settings = Settings()
    asyncio.run(apply_schema(settings))
    sys.stdout.write(f"schema applied at {settings.surreal_url}\n")
