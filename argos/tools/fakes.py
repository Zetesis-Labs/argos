"""Fakes en memoria de los puertos. Reproducen las mismas garantías que los adaptadores reales."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argos.core.messages import ConsumerSpec, JobMessage, decode_job_message
from argos.core.model import (
    Artifact,
    Attempt,
    Case,
    Chunk,
    Document,
    Extraction,
    Insert,
    Job,
    JobState,
    LedgerOp,
    LedgerRecord,
    OutboxEntry,
    OutboxState,
    Tenant,
    table_name,
)
from argos.core.ports import (
    BusUnavailableError,
    Delivery,
    LedgerConflictError,
    OutboundMessage,
    StoredObject,
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


def _unique_key(record: LedgerRecord) -> tuple[object, ...] | None:
    match record:
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

    async def delete_tenant_data(self, tenant_id: str) -> None:
        doomed = [
            key
            for key, row in self._rows.items()
            if (isinstance(row, Tenant) and row.id == tenant_id)
            or (not isinstance(row, Tenant) and row.tenant_id == tenant_id)
        ]
        for key in doomed:
            del self._rows[key]


class InMemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, content: AsyncIterable[bytes]) -> StoredObject:
        digest = hashlib.sha256()
        buffer = bytearray()
        async for chunk in content:
            digest.update(chunk)
            buffer.extend(chunk)
        self.objects[key] = bytes(buffer)
        return StoredObject(key=key, sha256=digest.hexdigest(), size=len(buffer))

    async def stat(self, key: str) -> StoredObject | None:
        data = self.objects.get(key)
        if data is None:
            return None
        return StoredObject(key=key, sha256=hashlib.sha256(data).hexdigest(), size=len(data))

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


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
