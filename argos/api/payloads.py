"""Lectura tipada de cuerpos JSON y formularios. Sin `Any`, así que sin modelos
de pydantic: lo que entra se comprueba campo a campo."""

from __future__ import annotations

import json
from typing import cast

from starlette.datastructures import UploadFile


def as_object(raw: bytes) -> dict[str, object] | None:
    try:
        decoded = cast(object, json.loads(raw or b"{}"))
    except ValueError:
        return None
    return cast(dict[str, object], decoded) if isinstance(decoded, dict) else None


def text_of(fields: dict[str, object], key: str, default: str = "") -> str:
    value = fields.get(key)
    return value if isinstance(value, str) else default


def optional_text(fields: dict[str, object], key: str) -> str | None:
    value = fields.get(key)
    return value if isinstance(value, str) and value else None


def strings_of(fields: dict[str, object], key: str) -> tuple[str, ...]:
    value = fields.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in cast(list[object], value) if isinstance(item, str))


def object_of(fields: dict[str, object], key: str) -> dict[str, object]:
    value = fields.get(key)
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def strings_in(fields: dict[str, object]) -> dict[str, str]:
    return {key: value for key, value in fields.items() if isinstance(value, str)}


def form_text(value: str | UploadFile | None) -> str | None:
    return value if isinstance(value, str) and value else None


def form_upload(value: str | UploadFile | None) -> UploadFile | None:
    return value if isinstance(value, UploadFile) else None
