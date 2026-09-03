"""Identificadores ordenables por tiempo: milisegundos en hex más 64 bits aleatorios."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


class TimeOrderedIds:
    def new_id(self) -> str:
        millis = int(datetime.now(UTC).timestamp() * 1000)
        return f"{millis:012x}{secrets.token_hex(8)}"
