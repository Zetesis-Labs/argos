"""Almacenamiento de Agno (sesiones, memoria) en agno/sessions (constitución §7)."""

from __future__ import annotations

from agno.db.surrealdb import SurrealDb

from argos.config import Settings


def build_agno_db(settings: Settings) -> SurrealDb:
    return SurrealDb(
        client=None,
        db_url=settings.surreal_ws_url,
        db_creds={
            "namespace": settings.agno_namespace,
            "database": settings.agno_database,
            "username": settings.surreal_runtime_user,
            "password": settings.surreal_runtime_password.get_secret_value(),
        },
        db_ns=settings.agno_namespace,
        db_db=settings.agno_database,
    )
