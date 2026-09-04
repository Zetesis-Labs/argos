"""Aplica db/schema.surql de forma idempotente (constitución §7)."""

from __future__ import annotations

import asyncio
import sys

from argos.config import WORKLOADS, Settings
from argos.platform.surreal import SurrealHttp

SCHEMA_VERSION = 5


def placeholder(name: str) -> str:
    return f"{{{{{name.upper()}_PASSWORD}}}}"


def render_schema(template: str, passwords: dict[str, str]) -> str:
    rendered = template
    for name, password in passwords.items():
        if "'" in password or "\\" in password:
            raise ValueError("Las contraseñas de SurrealDB no pueden contener comillas ni barras")
        rendered = rendered.replace(placeholder(name), password)
    remaining = [name for name in passwords if placeholder(name) in rendered]
    if remaining or "{{" in rendered:
        raise ValueError(f"El esquema dejó marcadores sin sustituir: {remaining}")
    return rendered


def schema_passwords(settings: Settings) -> dict[str, str]:
    passwords = {
        "agent": settings.surreal_agent_password.get_secret_value(),
        "runtime": settings.surreal_runtime_password.get_secret_value(),
    }
    for workload in WORKLOADS:
        passwords[workload] = settings.workload(workload).password.get_secret_value()
    return passwords


async def apply_schema(settings: Settings) -> None:
    template = settings.schema_path.read_text(encoding="utf-8")
    rendered = render_schema(template, schema_passwords(settings))
    await SurrealHttp(settings.surreal_url).sql(rendered, auth=settings.root_auth)


def main() -> None:
    settings = Settings()
    asyncio.run(apply_schema(settings))
    sys.stdout.write(f"schema applied at {settings.surreal_url}\n")
