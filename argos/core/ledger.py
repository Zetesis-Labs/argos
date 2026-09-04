"""Planes puros sobre el libro: qué filas cambian y con qué revisión. Ninguna de
estas funciones habla con SurrealDB; devuelven operaciones que otro confirma."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from argos.core.analysis import DraftEntity
from argos.core.model import (
    Case,
    CaseEntity,
    CaseState,
    Entity,
    Insert,
    LedgerOp,
    ReviewState,
    Update,
    case_entity_id,
    entity_id,
)


@dataclass(frozen=True)
class Obsolete:
    reason: str


@dataclass(frozen=True)
class PlannedNoticeCase:
    ops: tuple[LedgerOp, ...]
    case: Case


def entity_memory_ops(
    *,
    case: Case,
    entities: Sequence[DraftEntity],
    known: Mapping[str, Entity],
    linked: frozenset[str],
    now: datetime,
) -> tuple[LedgerOp, ...]:
    """La entidad es memoria: un mismo dominio o IBAN es un solo nodo entre casos."""
    ops: list[LedgerOp] = []
    seen: set[str] = set()
    for drafted in entities:
        identifier = entity_id(drafted.kind, drafted.value)
        if identifier in seen:
            continue
        seen.add(identifier)
        stored = known.get(identifier)
        if stored is None:
            ops.append(
                Insert(
                    Entity(
                        id=identifier,
                        kind=drafted.kind,
                        value=drafted.value,
                        strength=drafted.strength,
                        first_seen_at=now,
                        last_seen_at=now,
                        revision=0,
                    )
                )
            )
        else:
            ops.append(
                Update(
                    Entity(
                        id=stored.id,
                        kind=stored.kind,
                        value=stored.value,
                        strength=stored.strength,
                        first_seen_at=stored.first_seen_at,
                        last_seen_at=now,
                        revision=stored.revision + 1,
                    )
                )
            )
        if identifier not in linked:
            ops.append(
                Insert(
                    CaseEntity(
                        id=case_entity_id(case.id, identifier),
                        case_id=case.id,
                        entity_id=identifier,
                        created_at=now,
                        revision=0,
                    )
                )
            )
    return tuple(ops)


def plan_notice_case(
    *,
    notice_text: str,
    notice_links: Sequence[str],
    language: str | None,
    entities: Sequence[DraftEntity],
    known: Mapping[str, Entity],
    case_id: str,
    now: datetime,
) -> PlannedNoticeCase:
    case = Case(
        id=case_id,
        state=CaseState.RECEIVED,
        notice_text=notice_text,
        notice_links=tuple(notice_links),
        language=language,
        review_state=ReviewState.UNREVIEWED,
        reviewed_at=None,
        error=None,
        created_at=now,
        updated_at=now,
        revision=0,
    )
    ops: list[LedgerOp] = [Insert(case)]
    ops.extend(
        entity_memory_ops(case=case, entities=entities, known=known, linked=frozenset(), now=now)
    )
    return PlannedNoticeCase(ops=tuple(ops), case=case)
