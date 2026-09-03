"""Fakes en memoria de los puertos. Reproducen las mismas garantías que los adaptadores reales."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argos.core.messages import ConsumerSpec, JobMessage, decode_job_message
from argos.core.model import (
    Artifact,
    ArtifactState,
    Attempt,
    Case,
    CaseEntity,
    CaseState,
    Chunk,
    Delete,
    Document,
    DocumentState,
    Entity,
    EntityKind,
    EntityLink,
    Extraction,
    Insert,
    Job,
    JobCount,
    JobState,
    JobType,
    LedgerOp,
    LedgerRecord,
    OfficialWarning,
    OutboxEntry,
    OutboxState,
    Signal,
    Tenant,
    Verdict,
    VerdictState,
    table_name,
)
from argos.core.ports import (
    BusUnavailableError,
    CaseBrief,
    Delivery,
    Investigation,
    LedgerConflictError,
    ObjectMetadata,
    ObjectSizeMismatchError,
    ObjectTooLargeError,
    OpenPdf,
    OutboundMessage,
    PageOcr,
    PdfError,
    StoredObject,
    VerdictBrief,
)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class SequentialIds:
    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._next = 0

    def new_id(self) -> str:
        self._next += 1
        return f"{self._prefix}{self._next:04d}"


SHARED_RECORDS = (Entity, EntityLink, OfficialWarning)


def _unique_key(record: LedgerRecord) -> tuple[object, ...] | None:
    match record:
        case Entity():
            return ("entity", record.kind, record.value)
        case EntityLink():
            return ("entity_link", record.left_entity_id, record.right_entity_id)
        case CaseEntity():
            return ("case_entity", record.case_id, record.entity_id)
        case Verdict():
            return ("verdict", record.case_id, record.version)
        case Document():
            return ("document", record.case_id, record.sha256)
        case Attempt():
            return ("attempt", record.job_id, record.number)
        case OutboxEntry():
            return ("outbox_entry", record.message_id)
        case Extraction():
            return ("extraction", record.document_id, record.extractor_version, record.options)
        case Chunk():
            return ("chunk", record.extraction_id, record.position)
        case _:
            return None


class InMemoryLedger:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], LedgerRecord] = {}

    def _key(self, record: LedgerRecord) -> tuple[str, str]:
        return (table_name(record), record.id)

    def _check(self, ops: Sequence[LedgerOp]) -> None:
        planned_uniques: set[tuple[object, ...]] = set()
        planned_keys: set[tuple[str, str]] = set()
        for op in ops:
            key = self._key(op.record)
            if key in planned_keys:
                raise LedgerConflictError(f"{key} written twice in one transaction")
            planned_keys.add(key)
            if isinstance(op, Delete):
                continue
            if isinstance(op, Insert):
                if key in self._rows:
                    raise LedgerConflictError(f"{key} already exists")
                unique = _unique_key(op.record)
                if unique is not None:
                    if unique in planned_uniques or any(
                        _unique_key(row) == unique for row in self._rows.values()
                    ):
                        raise LedgerConflictError(f"unique violation {unique}")
                    planned_uniques.add(unique)
            else:
                stored = self._rows.get(key)
                if stored is None or stored.revision != op.record.revision - 1:
                    raise LedgerConflictError(f"{key} revision mismatch")

    async def commit(self, ops: Sequence[LedgerOp]) -> None:
        self._check(ops)
        for op in ops:
            if isinstance(op, Delete):
                self._rows.pop(self._key(op.record), None)
            else:
                self._rows[self._key(op.record)] = op.record

    def _all(self, table: str) -> list[LedgerRecord]:
        return [row for (name, _), row in self._rows.items() if name == table]

    async def tenant(self, tenant_id: str) -> Tenant | None:
        row = self._rows.get(("tenant", tenant_id))
        return row if isinstance(row, Tenant) else None

    async def case(self, case_id: str) -> Case | None:
        row = self._rows.get(("case", case_id))
        return row if isinstance(row, Case) else None

    async def case_by_notice(
        self, tenant_id: str, notice_hash: str, *, since: datetime
    ) -> Case | None:
        matches = [
            row
            for row in self._all("case")
            if isinstance(row, Case)
            and row.tenant_id == tenant_id
            and row.notice_hash == notice_hash
            and row.created_at >= since
        ]
        matches.sort(key=lambda row: row.created_at, reverse=True)
        return matches[0] if matches else None

    async def artifact(self, artifact_id: str) -> Artifact | None:
        row = self._rows.get(("artifact", artifact_id))
        return row if isinstance(row, Artifact) else None

    async def document(self, document_id: str) -> Document | None:
        row = self._rows.get(("document", document_id))
        return row if isinstance(row, Document) else None

    async def document_by_hash(self, case_id: str, sha256: str) -> Document | None:
        for row in self._all("document"):
            if isinstance(row, Document) and row.case_id == case_id and row.sha256 == sha256:
                return row
        return None

    async def documents_of_case(self, case_id: str) -> list[Document]:
        documents = [
            row
            for row in self._all("document")
            if isinstance(row, Document) and row.case_id == case_id
        ]
        documents.sort(key=lambda row: row.created_at)
        return documents

    async def job(self, job_id: str) -> Job | None:
        row = self._rows.get(("job", job_id))
        return row if isinstance(row, Job) else None

    async def jobs_of_case(self, case_id: str) -> list[Job]:
        jobs = [row for row in self._all("job") if isinstance(row, Job) and row.case_id == case_id]
        jobs.sort(key=lambda row: row.created_at)
        return jobs

    async def jobs_with_expired_lease(self, now: datetime) -> list[Job]:
        return [
            row
            for row in self._all("job")
            if isinstance(row, Job)
            and row.state is JobState.RUNNING
            and row.lease_until is not None
            and row.lease_until < now
        ]

    async def attempts(self, job_id: str) -> list[Attempt]:
        attempts = [
            row for row in self._all("attempt") if isinstance(row, Attempt) and row.job_id == job_id
        ]
        attempts.sort(key=lambda row: row.number)
        return attempts

    async def outbox_entry(self, entry_id: str) -> OutboxEntry | None:
        row = self._rows.get(("outbox_entry", entry_id))
        return row if isinstance(row, OutboxEntry) else None

    async def outbox_of_job(self, job_id: str) -> list[OutboxEntry]:
        entries = [
            row
            for row in self._all("outbox_entry")
            if isinstance(row, OutboxEntry) and row.job_id == job_id
        ]
        entries.sort(key=lambda row: (row.created_at, row.id))
        return entries

    async def pending_outbox(self, now: datetime, *, limit: int) -> list[OutboxEntry]:
        entries = [
            row
            for row in self._all("outbox_entry")
            if isinstance(row, OutboxEntry)
            and row.state is OutboxState.PENDING
            and row.not_before <= now
            and (row.lease_until is None or row.lease_until <= now)
        ]
        entries.sort(key=lambda row: (row.not_before, row.id))
        return entries[:limit]

    async def extraction(self, extraction_id: str) -> Extraction | None:
        row = self._rows.get(("extraction", extraction_id))
        return row if isinstance(row, Extraction) else None

    async def extractions_of_document(self, document_id: str) -> list[Extraction]:
        extractions = [
            row
            for row in self._all("extraction")
            if isinstance(row, Extraction) and row.document_id == document_id
        ]
        extractions.sort(key=lambda row: row.created_at)
        return extractions

    async def chunks(self, extraction_id: str) -> list[Chunk]:
        chunks = [
            row
            for row in self._all("chunk")
            if isinstance(row, Chunk) and row.extraction_id == extraction_id
        ]
        chunks.sort(key=lambda row: row.position)
        return chunks

    async def extractions_of_case(self, case_id: str) -> list[Extraction]:
        extractions = [
            row
            for row in self._all("extraction")
            if isinstance(row, Extraction) and row.case_id == case_id
        ]
        extractions.sort(key=lambda row: row.created_at)
        return extractions

    async def entity_by_value(self, kind: EntityKind, value: str) -> Entity | None:
        for row in self._all("entity"):
            if isinstance(row, Entity) and row.kind is kind and row.value == value:
                return row
        return None

    async def entities_of_case(self, case_id: str) -> list[CaseEntity]:
        links = [
            row
            for row in self._all("case_entity")
            if isinstance(row, CaseEntity) and row.case_id == case_id
        ]
        links.sort(key=lambda row: row.created_at)
        return links

    async def cases_of_entity(self, entity_id: str) -> list[CaseEntity]:
        links = [
            row
            for row in self._all("case_entity")
            if isinstance(row, CaseEntity) and row.entity_id == entity_id
        ]
        links.sort(key=lambda row: row.created_at)
        return links

    async def warnings_for(self, kind: EntityKind, value: str) -> list[OfficialWarning]:
        warnings = [
            row
            for row in self._all("warning")
            if isinstance(row, OfficialWarning)
            and row.entity_kind is kind
            and row.entity_value == value
        ]
        warnings.sort(key=lambda row: row.captured_at)
        return warnings

    async def stale_artifacts(self, now: datetime, *, limit: int) -> list[Artifact]:
        stale = [
            row
            for row in self._all("artifact")
            if isinstance(row, Artifact)
            and row.state is ArtifactState.UPLOADING
            and row.expires_at <= now
        ]
        stale.sort(key=lambda row: row.expires_at)
        return stale[:limit]

    async def expired_documents(self, now: datetime, *, limit: int) -> list[Document]:
        expired = [
            row
            for row in self._all("document")
            if isinstance(row, Document)
            and row.state is DocumentState.ACCEPTED
            and row.expires_at <= now
        ]
        expired.sort(key=lambda row: row.expires_at)
        return expired[:limit]

    async def job_counts(self) -> list[JobCount]:
        tally: dict[tuple[JobType, JobState], int] = {}
        for row in self._all("job"):
            if isinstance(row, Job):
                tally[(row.type, row.state)] = tally.get((row.type, row.state), 0) + 1
        return [
            JobCount(type=job_type, state=state, count=count)
            for (job_type, state), count in sorted(tally.items())
        ]

    async def oldest_queued_job(self) -> Job | None:
        queued = [
            row for row in self._all("job") if isinstance(row, Job) and row.state is JobState.QUEUED
        ]
        queued.sort(key=lambda row: row.created_at)
        return queued[0] if queued else None

    async def count_cases(self, states: Sequence[CaseState]) -> int:
        wanted = set(states)
        return sum(1 for row in self._all("case") if isinstance(row, Case) and row.state in wanted)

    async def signals_of_case(self, case_id: str) -> list[Signal]:
        signals = [
            row for row in self._all("signal") if isinstance(row, Signal) and row.case_id == case_id
        ]
        signals.sort(key=lambda row: (row.created_at, row.id))
        return signals

    async def current_verdict(self, case_id: str) -> Verdict | None:
        verdicts = [
            row
            for row in self._all("verdict")
            if isinstance(row, Verdict)
            and row.case_id == case_id
            and row.state is VerdictState.CURRENT
        ]
        verdicts.sort(key=lambda row: row.version, reverse=True)
        return verdicts[0] if verdicts else None

    async def delete_tenant_data(self, tenant_id: str) -> None:
        doomed = [key for key, row in self._rows.items() if _belongs_to(row, tenant_id)]
        for key in doomed:
            del self._rows[key]


def _belongs_to(record: LedgerRecord, tenant_id: str) -> bool:
    if isinstance(record, Tenant):
        return record.id == tenant_id
    if isinstance(record, SHARED_RECORDS):
        return False
    return record.tenant_id == tenant_id


class InMemoryObjectStore:
    """Exige el mismo tamaño declarado y la misma lectura acotada que RustFS."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(
        self, key: str, content: AsyncIterable[bytes], *, size: int, mime: str
    ) -> StoredObject:
        digest = hashlib.sha256()
        buffer = bytearray()
        async for chunk in content:
            digest.update(chunk)
            buffer.extend(chunk)
        if len(buffer) != size:
            raise ObjectSizeMismatchError(f"declared {size} bytes but streamed {len(buffer)}")
        self.objects[key] = (bytes(buffer), mime)
        return StoredObject(key=key, sha256=digest.hexdigest(), size=len(buffer))

    async def read(self, key: str, *, limit: int) -> bytes | None:
        stored = self.objects.get(key)
        if stored is None:
            return None
        if len(stored[0]) > limit:
            raise ObjectTooLargeError(f"{key} exceeds {limit} bytes")
        return stored[0]

    async def stat(self, key: str) -> ObjectMetadata | None:
        stored = self.objects.get(key)
        if stored is None:
            return None
        return ObjectMetadata(key=key, size=len(stored[0]), mime=stored[1])

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def presigned_get(self, key: str, *, expires_in: timedelta) -> str:
        expiry = int(expires_in.total_seconds())
        return f"memory://{key}?expires_in={expiry}"


@dataclass
class _Queued:
    message: OutboundMessage
    deliveries: int = 0
    inflight: bool = False
    acked: bool = False


class FakeDelivery:
    def __init__(self, queued: _Queued) -> None:
        self._queued = queued
        self._message = decode_job_message(queued.message.payload)

    @property
    def message(self) -> JobMessage:
        return self._message

    @property
    def subject(self) -> str:
        return self._queued.message.subject

    @property
    def delivery_count(self) -> int:
        return self._queued.deliveries

    async def ack(self) -> None:
        self._queued.inflight = False
        self._queued.acked = True

    async def nak(self) -> None:
        self._queued.inflight = False


class FakeDeliveries:
    def __init__(self, queue: list[_Queued], subjects: Sequence[str], max_deliveries: int) -> None:
        self._queue = queue
        self._subjects = tuple(subjects)
        self._max_deliveries = max_deliveries

    def _deliverable(self, queued: _Queued) -> bool:
        return (
            queued.message.subject in self._subjects
            and not queued.acked
            and not queued.inflight
            and queued.deliveries < self._max_deliveries
        )

    async def fetch(self, *, limit: int, timeout: float) -> Sequence[Delivery]:
        ready = [queued for queued in self._queue if self._deliverable(queued)][:limit]
        for queued in ready:
            queued.inflight = True
            queued.deliveries += 1
        return [FakeDelivery(queued) for queued in ready]


class RecordingOcr:
    """Registra cada página que se le pide; delega en un OCR real o devuelve un texto fijo."""

    def __init__(self, inner: PageOcr | None = None, *, text: str = "") -> None:
        self._inner = inner
        self._text = text
        self.calls: list[int] = []

    def text_of(self, image: bytes, *, language: str) -> str:
        self.calls.append(len(image))
        if self._inner is not None:
            return self._inner.text_of(image, language=language)
        return self._text


class StubPdf:
    def __init__(self, pages: Sequence[str]) -> None:
        self._pages = tuple(pages)
        self.closed = False

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def text_of(self, number: int) -> str:
        return self._pages[number - 1]

    def image_of(self, number: int, *, scale: float) -> bytes:
        return f"page-{number}@{scale}".encode()

    def close(self) -> None:
        self.closed = True


class StubPdfReader:
    """Devuelve páginas fijas o el fallo permanente que se le pida."""

    def __init__(self, pages: Sequence[str] = (), *, error: PdfError | None = None) -> None:
        self._pages = tuple(pages)
        self._error = error
        self.opened: list[int] = []

    def open(self, data: bytes) -> OpenPdf:
        self.opened.append(len(data))
        if self._error is not None:
            raise self._error
        return StubPdf(self._pages)


class FakeBus:
    """Publica con la misma deduplicación por `message_id` y reentrega que JetStream."""

    def __init__(self, *, failures: int = 0, max_deliveries: int = 3) -> None:
        self.published: list[OutboundMessage] = []
        self.failures_remaining = failures
        self._max_deliveries = max_deliveries
        self._queue: list[_Queued] = []
        self._seen: set[str] = set()

    async def publish(self, message: OutboundMessage) -> None:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise BusUnavailableError("bus rejected the publication")
        if message.message_id in self._seen:
            return
        self._seen.add(message.message_id)
        self.published.append(message)
        self._queue.append(_Queued(message=message))

    async def deliveries(
        self, spec: ConsumerSpec, *, ack_wait: timedelta | None = None
    ) -> FakeDeliveries:
        return FakeDeliveries(self._queue, spec.subjects, self._max_deliveries)

    async def purge(self, *streams: str) -> None:
        self._queue.clear()
        self._seen.clear()
        self.published.clear()


class ScriptedInvestigator:
    """Devuelve una investigación fija y guarda lo que se le pidió."""

    def __init__(self, result: Investigation) -> None:
        self._result = result
        self.briefs: list[CaseBrief] = []

    async def investigate(self, brief: CaseBrief) -> Investigation:
        self.briefs.append(brief)
        return self._result


class ScriptedNarrator:
    def __init__(self, summary: str = "Resumen de prueba con indicios.") -> None:
        self._summary = summary
        self.briefs: list[VerdictBrief] = []

    async def narrate(self, brief: VerdictBrief) -> str:
        self.briefs.append(brief)
        return self._summary
