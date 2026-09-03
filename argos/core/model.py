"""Registros del libro operacional de argos/ops (S02 §6)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class CaseState(StrEnum):
    RECEIVED = "received"
    AWAITING_PROCESSING = "awaiting_processing"
    ANALYZING = "analyzing"
    VERDICT_ISSUED = "verdict_issued"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    FAILED = "failed"


TERMINAL_CASE_STATES = frozenset(
    {CaseState.VERDICT_ISSUED, CaseState.PARTIAL, CaseState.INSUFFICIENT, CaseState.FAILED}
)


class DocumentState(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ArtifactState(StrEnum):
    UPLOADING = "uploading"
    AVAILABLE = "available"
    DELETED = "deleted"


class JobType(StrEnum):
    DOCUMENT_EXTRACT = "document.extract"
    CASE_ANALYZE = "case.analyze"
    SOURCE_INGEST = "source.ingest"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AttemptState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LOST = "lost"


class FailureKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class OutboxKind(StrEnum):
    COMMAND = "command"
    EVENT = "event"


class OutboxState(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"


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
    DOMAIN = "domain"
    PATTERNS = "patterns"
    MEMORY = "memory"
    DOCUMENT = "document"


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


class ExtractionState(StrEnum):
    AVAILABLE = "available"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    active: bool
    revision: int


@dataclass(frozen=True)
class Case:
    id: str
    tenant_id: str
    state: CaseState
    notice_hash: str | None
    language: str | None
    correlation_id: str
    previous_case_id: str | None
    review_state: ReviewState
    reviewed_at: datetime | None
    reviewed_by: str | None
    created_at: datetime
    updated_at: datetime
    revision: int


@dataclass(frozen=True)
class Artifact:
    id: str
    tenant_id: str
    case_id: str
    bucket: str
    key: str
    state: ArtifactState
    sha256: str | None
    size: int
    mime: str
    created_at: datetime
    expires_at: datetime
    revision: int


@dataclass(frozen=True)
class Document:
    id: str
    tenant_id: str
    case_id: str
    artifact_id: str
    sha256: str
    mime: str
    size: int
    page_count: int | None
    state: DocumentState
    created_at: datetime
    expires_at: datetime
    revision: int


@dataclass(frozen=True)
class Job:
    id: str
    tenant_id: str
    case_id: str
    type: JobType
    document_id: str | None
    state: JobState
    attempt: int
    max_attempts: int
    lease_until: datetime | None
    extractor_version: str
    options: str
    public_error: str | None
    internal_error: str | None
    correlation_id: str
    previous_job_id: str | None
    created_at: datetime
    updated_at: datetime
    revision: int


@dataclass(frozen=True)
class Attempt:
    id: str
    tenant_id: str
    job_id: str
    number: int
    consumer: str
    state: AttemptState
    started_at: datetime
    finished_at: datetime | None
    lease_until: datetime
    error_kind: FailureKind | None
    error_code: str | None
    revision: int


@dataclass(frozen=True)
class OutboxEntry:
    id: str
    tenant_id: str
    job_id: str
    kind: OutboxKind
    subject: str
    message_id: str
    attempt: int
    state: OutboxState
    not_before: datetime
    lease_until: datetime | None
    published_at: datetime | None
    created_at: datetime
    revision: int


@dataclass(frozen=True)
class Extraction:
    id: str
    tenant_id: str
    case_id: str
    document_id: str
    extractor_version: str
    options: str
    state: ExtractionState
    sha256: str
    page_count: int
    ocr_pages: int
    text_artifact_id: str
    manifest_artifact_id: str
    created_at: datetime
    expires_at: datetime
    revision: int


@dataclass(frozen=True)
class Chunk:
    id: str
    tenant_id: str
    extraction_id: str
    page: int
    position: int
    text: str
    sha256: str
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
    Tenant
    | Case
    | Artifact
    | Document
    | Job
    | Attempt
    | OutboxEntry
    | Extraction
    | Chunk
    | Entity
    | EntityLink
    | CaseEntity
    | OfficialWarning
    | Signal
    | Verdict
)

TABLE_NAMES: dict[type[LedgerRecord], str] = {
    Tenant: "tenant",
    Case: "case",
    Artifact: "artifact",
    Document: "document",
    Job: "job",
    Attempt: "attempt",
    OutboxEntry: "outbox_entry",
    Extraction: "extraction",
    Chunk: "chunk",
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
class JobCount:
    """Proyección del libro para métricas (S02 §13), no una fila."""

    type: JobType
    state: JobState
    count: int


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


def attempt_id(job_id: str, number: int) -> str:
    return f"{job_id}-{number}"


def command_entry_id(job_id: str, attempt: int) -> str:
    return f"cmd-{job_id}-{attempt}"


def event_entry_id(job_id: str, attempt: int) -> str:
    return f"evt-{job_id}-{attempt}"


def message_id(job_id: str, attempt: int) -> str:
    return f"{job_id}:{attempt}"


def analysis_job_id(case_id: str, sequence: int) -> str:
    return f"{case_id}-analyze-{sequence}"


def entity_id(kind: EntityKind, value: str) -> str:
    return f"{kind.value}-{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def case_entity_id(case_id: str, entity: str) -> str:
    return f"{case_id}-{entity}"


def verdict_id(case_id: str, version: int) -> str:
    return f"{case_id}-verdict-{version}"
