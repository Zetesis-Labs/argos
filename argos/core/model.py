"""Registros del libro operacional de argos/ops (S02 §6)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CaseState(StrEnum):
    RECEIVED = "received"
    ANALYZING = "analyzing"
    VERDICT_ISSUED = "verdict_issued"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    FAILED = "failed"


TERMINAL_CASE_STATES = frozenset(
    {CaseState.VERDICT_ISSUED, CaseState.PARTIAL, CaseState.INSUFFICIENT, CaseState.FAILED}
)


class ReviewState(StrEnum):
    UNREVIEWED = "unreviewed"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    INCONCLUSIVE = "inconclusive"


class EntityKind(StrEnum):
    DOMAIN = "domain"
    PHONE = "phone"
    EMAIL = "email"
    IBAN = "iban"
    WALLET = "wallet"
    HANDLE = "handle"
    COMPANY = "company"


class Strength(StrEnum):
    STRONG = "strong"
    WEAK = "weak"


class Analysis(StrEnum):
    TRIAGE = "triage"
    REGISTRIES = "registries"
    PATTERNS = "patterns"
    MEMORY = "memory"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNDETERMINED = "undetermined"


class VerdictOutcome(StrEnum):
    ISSUED = "verdict_issued"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class VerdictState(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    active: bool
    revision: int


@dataclass(frozen=True)
class Case:
    """El aviso vive en su caso: sin su texto no hay nada que analizar (R1, R8)."""

    id: str
    tenant_id: str
    state: CaseState
    notice_hash: str | None
    notice_text: str
    notice_links: tuple[str, ...]
    language: str | None
    correlation_id: str
    previous_case_id: str | None
    review_state: ReviewState
    reviewed_at: datetime | None
    reviewed_by: str | None
    public_error: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    revision: int


@dataclass(frozen=True)
class Entity:
    """Memoria compartida entre tenants (constitución §6): no lleva tenant."""

    id: str
    kind: EntityKind
    value: str
    strength: Strength
    first_seen_at: datetime
    last_seen_at: datetime
    revision: int


@dataclass(frozen=True)
class EntityLink:
    id: str
    left_entity_id: str
    right_entity_id: str
    reason: str
    created_at: datetime
    revision: int


@dataclass(frozen=True)
class CaseEntity:
    id: str
    tenant_id: str
    case_id: str
    entity_id: str
    created_at: datetime
    revision: int


@dataclass(frozen=True)
class OfficialWarning:
    id: str
    regulator: str
    url: str
    entity_kind: EntityKind
    entity_value: str
    active: bool
    captured_at: datetime
    revision: int


@dataclass(frozen=True)
class Signal:
    id: str
    tenant_id: str
    case_id: str
    analysis: Analysis
    code: str
    strength: Strength
    official: bool
    recidivism: bool
    source: str
    observed_at: datetime
    value: str
    quote: str
    created_at: datetime
    revision: int


@dataclass(frozen=True)
class Verdict:
    id: str
    tenant_id: str
    case_id: str
    version: int
    level: RiskLevel
    outcome: VerdictOutcome
    state: VerdictState
    language: str
    summary: str
    actions: tuple[str, ...]
    missing: tuple[str, ...]
    created_at: datetime
    revision: int


LedgerRecord = (
    Tenant | Case | Entity | EntityLink | CaseEntity | OfficialWarning | Signal | Verdict
)

TABLE_NAMES: dict[type[LedgerRecord], str] = {
    Tenant: "tenant",
    Case: "case",
    Entity: "entity",
    EntityLink: "entity_link",
    CaseEntity: "case_entity",
    OfficialWarning: "warning",
    Signal: "signal",
    Verdict: "verdict",
}


def table_name(record: LedgerRecord) -> str:
    return TABLE_NAMES[type(record)]


@dataclass(frozen=True)
class Insert:
    record: LedgerRecord


@dataclass(frozen=True)
class Update:
    """Escritura condicional: solo aplica si la fila guardada tiene `record.revision - 1`."""

    record: LedgerRecord


@dataclass(frozen=True)
class Delete:
    """Solo para lo que guarda contenido: el resto caduca en su sitio como evidencia."""

    record: LedgerRecord


LedgerOp = Insert | Update | Delete


def entity_id(kind: EntityKind, value: str) -> str:
    return f"{kind.value}-{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def case_entity_id(case_id: str, entity: str) -> str:
    return f"{case_id}-{entity}"


def verdict_id(case_id: str, version: int) -> str:
    return f"{case_id}-verdict-{version}"
