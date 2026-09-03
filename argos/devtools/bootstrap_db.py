"""Aplica db/schema.surql de forma idempotente (constitución §7)."""

from __future__ import annotations

import asyncio
import sys

from argos.config import Settings
from argos.platform.surreal import SurrealHttp

SCHEMA_VERSION = 2
PLACEHOLDERS = ("{{AGENT_PASSWORD}}", "{{RUNTIME_PASSWORD}}", "{{LEDGER_PASSWORD}}")


def render_schema(
    template: str, *, agent_password: str, runtime_password: str, ledger_password: str
) -> str:
    passwords = (agent_password, runtime_password, ledger_password)
    for password in passwords:
        if "'" in password or "\\" in password:
            raise ValueError("Las contraseñas de SurrealDB no pueden contener comillas ni barras")
    rendered = template
    for placeholder, password in zip(PLACEHOLDERS, passwords, strict=True):
        rendered = rendered.replace(placeholder, password)
    return rendered


async def apply_schema(settings: Settings) -> None:
    template = settings.schema_path.read_text(encoding="utf-8")
    rendered = render_schema(
        template,
        agent_password=settings.surreal_agent_password.get_secret_value(),
        runtime_password=settings.surreal_runtime_password.get_secret_value(),
        ledger_password=settings.surreal_ledger_password.get_secret_value(),
    )
    await SurrealHttp(settings.surreal_url).sql(rendered, auth=settings.root_auth)


def main() -> None:
    settings = Settings()
    asyncio.run(apply_schema(settings))
    sys.stdout.write(f"schema applied at {settings.surreal_url}\n")
