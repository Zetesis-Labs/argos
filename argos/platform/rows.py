"""Conversión entre registros del núcleo y filas de SurrealDB, sin `Any`."""

from __future__ import annotations

import types
from dataclasses import fields
from datetime import datetime
from enum import StrEnum
from typing import Protocol, cast, get_args, get_origin, get_type_hints

from surrealdb import RecordID

from argos.core.model import LedgerRecord
from argos.core.ports import LedgerError

Row = dict[str, object]


class RowConstructor[R_co: LedgerRecord](Protocol):
    def __call__(self, **kwargs: object) -> R_co: ...


def to_row(record: LedgerRecord) -> Row:
    row: Row = {}
    for field in fields(record):
        if field.name == "id":
            continue
        value = cast(object, getattr(record, field.name))
        if isinstance(value, StrEnum):
            row[field.name] = value.value
        elif isinstance(value, tuple):
            row[field.name] = list(cast(tuple[object, ...], value))
        else:
            row[field.name] = value
    return row


def _optional_inner(hint: object) -> tuple[object, bool]:
    if isinstance(hint, types.UnionType):
        members = [member for member in hint.__args__ if member is not type(None)]
        if len(members) == 1 and len(hint.__args__) == 2:
            return members[0], True
    return hint, False


def _convert(name: str, value: object, hint: object) -> object:
    inner, optional = _optional_inner(hint)
    if value is None:
        if optional:
            return None
        raise LedgerError(f"field {name} is missing")
    if isinstance(inner, type) and issubclass(inner, StrEnum):
        if not isinstance(value, str):
            raise LedgerError(f"field {name} is not a string")
        return inner(value)
    if inner is datetime:
        if not isinstance(value, datetime):
            raise LedgerError(f"field {name} is not a datetime")
        return value
    if inner is bool:
        if not isinstance(value, bool):
            raise LedgerError(f"field {name} is not a bool")
        return value
    if inner is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise LedgerError(f"field {name} is not an int")
        return value
    if inner is str:
        if not isinstance(value, str):
            raise LedgerError(f"field {name} is not a string")
        return value
    if get_origin(inner) is tuple:
        return _tuple_of(name, value, get_args(inner))
    raise LedgerError(f"field {name} has an unsupported type {hint!r}")


def _tuple_of(name: str, value: object, args: tuple[object, ...]) -> tuple[object, ...]:
    if args != (str, Ellipsis):
        raise LedgerError(f"field {name} has an unsupported tuple type")
    if not isinstance(value, list):
        raise LedgerError(f"field {name} is not a list")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise LedgerError(f"field {name} has a non-string item")
    return tuple(cast(list[str], items))


def record_id_of(value: object) -> str:
    if isinstance(value, RecordID):
        identifier = cast(object, value.id)
        if isinstance(identifier, str):
            return identifier
    if isinstance(value, str):
        return value
    raise LedgerError(f"unexpected record id {value!r}")


def from_row[R: LedgerRecord](cls: type[R], row: Row) -> R:
    hints = get_type_hints(cls)
    kwargs: Row = {}
    for field in fields(cls):
        if field.name == "id":
            kwargs["id"] = record_id_of(row.get("id"))
            continue
        kwargs[field.name] = _convert(field.name, row.get(field.name), hints[field.name])
    constructor = cast(RowConstructor[R], cls)
    return constructor(**kwargs)
