"""Carga advertencias sintéticas para desarrollo sin simular la ingesta de S07."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

from argos.config import Settings
from argos.core.analysis import normalized_identifier
from argos.core.model import EntityKind, Insert, LedgerOp, OfficialWarning, Update
from argos.core.ports import Ledger
from argos.platform.ledger import ledger_for

DEFAULT_FIXTURE = Path(__file__).parents[2] / "tests" / "fixtures" / "synthetic_warnings.json"
WARNING_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


class WarningFixtureError(ValueError):
    pass


@dataclass(frozen=True)
class WarningSeedReport:
    inserted: int
    updated: int
    unchanged: int


def _required_text(entry: dict[str, object], field: str, *, position: int) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise WarningFixtureError(f"advertencia {position}: {field} es obligatorio")
    return value.strip()


def _captured_at(entry: dict[str, object], *, position: int) -> datetime:
    raw = _required_text(entry, "captured_at", position=position)
    try:
        captured_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise WarningFixtureError(
            f"advertencia {position}: captured_at no es una fecha ISO 8601"
        ) from error
    if captured_at.tzinfo is None:
        raise WarningFixtureError(f"advertencia {position}: captured_at necesita zona horaria")
    return captured_at.astimezone(UTC)


def _warning(entry: dict[str, object], *, position: int) -> OfficialWarning:
    warning_id = _required_text(entry, "id", position=position)
    if WARNING_ID.fullmatch(warning_id) is None:
        raise WarningFixtureError(f"advertencia {position}: id no es estable")
    url = _required_text(entry, "url", position=position)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname is None or not parsed.hostname.endswith(".example"):
        raise WarningFixtureError(
            f"advertencia {position}: url debe ser HTTPS bajo el dominio sintético .example"
        )
    raw_kind = _required_text(entry, "entity_kind", position=position)
    try:
        kind = EntityKind(raw_kind)
    except ValueError as error:
        raise WarningFixtureError(
            f"advertencia {position}: entity_kind no está soportado"
        ) from error
    active = entry.get("active")
    if not isinstance(active, bool):
        raise WarningFixtureError(f"advertencia {position}: active debe ser booleano")
    value = normalized_identifier(
        kind, _required_text(entry, "entity_value", position=position)
    )
    return OfficialWarning(
        id=warning_id,
        regulator=_required_text(entry, "regulator", position=position),
        url=url,
        entity_kind=kind,
        entity_value=value,
        active=active,
        captured_at=_captured_at(entry, position=position),
        revision=0,
    )


def load_warning_fixture(path: Path) -> tuple[OfficialWarning, ...]:
    try:
        decoded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise WarningFixtureError(f"no se pudo leer el fixture {path}: {error}") from error
    if not isinstance(decoded, list):
        raise WarningFixtureError("el fixture debe contener una lista")
    warnings: list[OfficialWarning] = []
    seen: set[str] = set()
    for position, raw in enumerate(cast(list[object], decoded), start=1):
        if not isinstance(raw, dict):
            raise WarningFixtureError(f"advertencia {position}: debe ser un objeto")
        warning = _warning(cast(dict[str, object], raw), position=position)
        if warning.id in seen:
            raise WarningFixtureError(f"advertencia {position}: id duplicado {warning.id}")
        seen.add(warning.id)
        warnings.append(warning)
    if not warnings:
        raise WarningFixtureError("el fixture no puede estar vacío")
    return tuple(warnings)


async def seed_warnings(
    ledger: Ledger, warnings: tuple[OfficialWarning, ...]
) -> WarningSeedReport:
    ops: list[LedgerOp] = []
    inserted = 0
    updated = 0
    unchanged = 0
    for warning in warnings:
        current = await ledger.warning(warning.id)
        if current is None:
            ops.append(Insert(warning))
            inserted += 1
        elif current == replace(warning, revision=current.revision):
            unchanged += 1
        else:
            ops.append(Update(replace(warning, revision=current.revision + 1)))
            updated += 1
    await ledger.commit(ops)
    return WarningSeedReport(inserted=inserted, updated=updated, unchanged=unchanged)


async def run(settings: Settings, fixture: Path = DEFAULT_FIXTURE) -> WarningSeedReport:
    ledger = ledger_for(settings, "gateway")
    await ledger.connect()
    try:
        return await seed_warnings(ledger, load_warning_fixture(fixture))
    finally:
        await ledger.close()


def main() -> None:
    report = asyncio.run(run(Settings()))
    sys.stdout.write(
        f"advertencias: {report.inserted} creadas, {report.updated} actualizadas, "
        f"{report.unchanged} sin cambios\n"
    )
