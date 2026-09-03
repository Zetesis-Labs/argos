"""Puertos del núcleo (constitución §3). Cada uno tiene un adaptador real y un fake."""

from __future__ import annotations

from collections.abc import AsyncIterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from argos.core.analysis import DraftEntity, DraftSignal
from argos.core.messages import JobMessage
from argos.core.model import (
    Artifact,
    Attempt,
    Case,
    CaseEntity,
    Chunk,
    Document,
    Entity,
    EntityKind,
    Extraction,
    Job,
    LedgerOp,
    OfficialWarning,
    OutboxEntry,
    RiskLevel,
    Signal,
    Tenant,
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

    async def tenant(self, tenant_id: str) -> Tenant | None: ...

    async def case(self, case_id: str) -> Case | None: ...

    async def case_by_notice(
        self, tenant_id: str, notice_hash: str, *, since: datetime
    ) -> Case | None: ...

    async def artifact(self, artifact_id: str) -> Artifact | None: ...

    async def document(self, document_id: str) -> Document | None: ...

    async def document_by_hash(self, case_id: str, sha256: str) -> Document | None: ...

    async def documents_of_case(self, case_id: str) -> list[Document]: ...

    async def job(self, job_id: str) -> Job | None: ...

    async def jobs_of_case(self, case_id: str) -> list[Job]: ...

    async def jobs_with_expired_lease(self, now: datetime) -> list[Job]: ...

    async def attempts(self, job_id: str) -> list[Attempt]: ...

    async def outbox_entry(self, entry_id: str) -> OutboxEntry | None: ...

    async def outbox_of_job(self, job_id: str) -> list[OutboxEntry]: ...

    async def pending_outbox(self, now: datetime, *, limit: int) -> list[OutboxEntry]: ...

    async def extraction(self, extraction_id: str) -> Extraction | None: ...

    async def extractions_of_document(self, document_id: str) -> list[Extraction]: ...

    async def extractions_of_case(self, case_id: str) -> list[Extraction]: ...

    async def chunks(self, extraction_id: str) -> list[Chunk]: ...

    async def entity_by_value(self, kind: EntityKind, value: str) -> Entity | None: ...

    async def entities_of_case(self, case_id: str) -> list[CaseEntity]: ...

    async def cases_of_entity(self, entity_id: str) -> list[CaseEntity]: ...

    async def warnings_for(self, kind: EntityKind, value: str) -> list[OfficialWarning]: ...

    async def signals_of_case(self, case_id: str) -> list[Signal]: ...

    async def current_verdict(self, case_id: str) -> Verdict | None: ...

    async def delete_tenant_data(self, tenant_id: str) -> None: ...


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    size: int
    mime: str


class ObjectStoreError(RuntimeError):
    pass


class ObjectSizeMismatchError(ObjectStoreError):
    """Lo subido no coincide con el tamaño declarado: no queda objeto utilizable."""


class ObjectTooLargeError(ObjectStoreError):
    pass


class S3ObjectStore(Protocol):
    """Puerto neutral S3 (S02 §10). Solo las operaciones que Argos necesita."""

    async def put(
        self, key: str, content: AsyncIterable[bytes], *, size: int, mime: str
    ) -> StoredObject: ...

    async def read(self, key: str, *, limit: int) -> bytes | None: ...

    async def stat(self, key: str) -> ObjectMetadata | None: ...

    async def delete(self, key: str) -> None: ...

    def presigned_get(self, key: str, *, expires_in: timedelta) -> str: ...


@dataclass(frozen=True)
class OutboundMessage:
    subject: str
    message_id: str
    payload: bytes
    headers: Mapping[str, str]


class BusUnavailableError(Exception):
    pass


class MessageBus(Protocol):
    async def publish(self, message: OutboundMessage) -> None: ...


class Delivery(Protocol):
    """Una entrega concreta. Se confirma solo después de persistir (constitución §9)."""

    @property
    def message(self) -> JobMessage: ...

    @property
    def subject(self) -> str: ...

    @property
    def delivery_count(self) -> int: ...

    async def ack(self) -> None: ...

    async def nak(self) -> None: ...


class MessageSource(Protocol):
    async def fetch(self, *, limit: int, timeout: float) -> Sequence[Delivery]: ...


class PdfError(Exception):
    """Fallo permanente del documento: el reintento no lo arregla (R19)."""

    code: str = "pdf.unreadable"


class PdfDamagedError(PdfError):
    code = "pdf.damaged"


class PdfEncryptedError(PdfError):
    code = "pdf.encrypted"


class PdfTooManyPagesError(PdfError):
    code = "pdf.too_many_pages"


class OpenPdf(Protocol):
    """Un documento abierto una sola vez: el texto y la imagen de cada página."""

    @property
    def page_count(self) -> int: ...

    def text_of(self, number: int) -> str: ...

    def image_of(self, number: int, *, scale: float) -> bytes: ...

    def close(self) -> None: ...


class PdfReader(Protocol):
    def open(self, data: bytes) -> OpenPdf: ...


class PageOcr(Protocol):
    def text_of(self, image: bytes, *, language: str) -> str: ...


@dataclass(frozen=True)
class ExtractionRef:
    extraction_id: str
    document_id: str
    page_count: int


@dataclass(frozen=True)
class CaseBrief:
    """Lo que el clúster de agentes recibe: referencias, nunca el documento (R8)."""

    tenant_id: str
    case_id: str
    language: str
    correlation_id: str
    extractions: tuple[ExtractionRef, ...]
    missing: tuple[str, ...]


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
