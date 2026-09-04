"""Puertos del núcleo (constitución §3). Cada uno tiene un adaptador real y un fake."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from argos.core.analysis import DraftEntity, DraftSignal
from argos.core.knowledge import KnowledgeBundle, KnowledgeSnapshot
from argos.core.model import (
    Case,
    CaseEntity,
    Entity,
    EntityKind,
    LedgerOp,
    OfficialWarning,
    RiskLevel,
    Signal,
    Verdict,
    VerdictOutcome,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdSource(Protocol):
    def new_id(self) -> str: ...


class LedgerConflictError(Exception):
    """Una escritura condicional no encontró la revisión esperada o violó una unicidad."""


class LedgerError(RuntimeError):
    pass


class Ledger(Protocol):
    async def commit(self, ops: Sequence[LedgerOp]) -> None: ...

    async def case(self, case_id: str) -> Case | None: ...

    async def entity_by_value(self, kind: EntityKind, value: str) -> Entity | None: ...

    async def entities_of_case(self, case_id: str) -> list[CaseEntity]: ...

    async def cases_of_entity(self, entity_id: str) -> list[CaseEntity]: ...

    async def warning(self, warning_id: str) -> OfficialWarning | None: ...

    async def warnings_for(self, kind: EntityKind, value: str) -> list[OfficialWarning]: ...

    async def signals_of_case(self, case_id: str) -> list[Signal]: ...

    async def current_verdict(self, case_id: str) -> Verdict | None: ...

    async def delete_case(self, case_id: str) -> None: ...


class KnowledgeProjection(Protocol):
    async def activate(
        self,
        snapshot: KnowledgeSnapshot,
        bundle: KnowledgeBundle,
        warnings: Sequence[OfficialWarning],
    ) -> bool: ...


@dataclass(frozen=True)
class CaseBrief:
    """Lo que el investigador recibe: el aviso y los identificadores ya extraídos."""

    case_id: str
    language: str
    text: str
    links: tuple[str, ...]
    entities: tuple[DraftEntity, ...]


@dataclass(frozen=True)
class Investigation:
    signals: tuple[DraftSignal, ...]
    entities: tuple[DraftEntity, ...]
    missing: tuple[str, ...]


class Investigator(Protocol):
    async def investigate(self, brief: CaseBrief) -> Investigation: ...


@dataclass(frozen=True)
class VerdictBrief:
    case_id: str
    language: str
    level: RiskLevel
    outcome: VerdictOutcome
    actions: tuple[str, ...]
    missing: tuple[str, ...]
    signals: tuple[DraftSignal, ...]


class Narrator(Protocol):
    """Redacta la explicación. No puede cambiar el nivel: lo recibe ya calculado."""

    async def narrate(self, brief: VerdictBrief) -> str: ...


@dataclass(frozen=True)
class ConversationBrief:
    """W2: la conversación se apoya en el veredicto y su evidencia, no en el original."""

    case_id: str
    language: str
    question: str
    level: RiskLevel
    outcome: VerdictOutcome
    summary: str
    actions: tuple[str, ...]
    quotes: tuple[str, ...]


class CaseAdvisor(Protocol):
    async def answer(self, brief: ConversationBrief) -> str: ...
