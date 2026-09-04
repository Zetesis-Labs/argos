"""Apoyo compartido por los tests: lectura de INFO FOR DB."""

from __future__ import annotations

from argos.platform.surreal import JsonValue


def names_in(section: JsonValue | None) -> set[str]:
    return set(section.keys()) if isinstance(section, dict) else set()
