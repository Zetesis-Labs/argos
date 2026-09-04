"""Lee el bundle versionado de OKF y activa su proyección en SurrealDB."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from argos.config import Settings
from argos.platform.knowledge import SurrealKnowledgeProjection
from argos.usecases.knowledge import KnowledgeImportReport, activate_knowledge

MAX_BUNDLE_BYTES = 10 * 1024 * 1024


def read_bundle(path: Path) -> bytes:
    content = path.read_bytes()
    if len(content) > MAX_BUNDLE_BYTES:
        raise ValueError("el bundle de conocimiento supera 10 MiB")
    return content


async def project_knowledge(settings: Settings) -> KnowledgeImportReport:
    content = read_bundle(settings.knowledge_graph_path)
    return await activate_knowledge(
        content,
        SurrealKnowledgeProjection(settings),
        imported_at=datetime.now(UTC),
    )


def main() -> None:
    report = asyncio.run(project_knowledge(Settings()))
    action = "activado" if report.changed else "sin cambios"
    sys.stdout.write(
        f"conocimiento {action}: {report.nodes} nodos, {report.edges} relaciones, "
        f"{report.warnings} advertencias ({report.source_head[:12]})\n"
    )
