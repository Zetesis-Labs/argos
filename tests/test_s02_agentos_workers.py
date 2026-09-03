"""Casos S02.1 a S02.11: libro de trabajos, outbox e ingreso de documentos y avisos."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import zstandard

from argos.config import Settings
from argos.core.keys import extraction_manifest_key, extraction_text_key, source_document_key
from argos.core.ledger import ATTEMPTS_EXHAUSTED, LEASE_LOST
from argos.core.messages import (
    CASE_COMPLETED_SUBJECT,
    CONSUMERS,
    DOCUMENT_EXTRACTED_SUBJECT,
    DOCUMENT_EXTRACTOR,
    DOCUMENT_FAILED_SUBJECT,
    EVENTS_STREAM,
    JOB_SUBJECTS,
    JOBS_STREAM,
    MESSAGE_ID_HEADER,
    JobMessage,
    decode_job_message,
)
from argos.core.model import (
    ArtifactState,
    AttemptState,
    CaseState,
    DocumentState,
    ExtractionState,
    FailureKind,
    Insert,
    JobState,
    JobType,
    OutboxKind,
    OutboxState,
    Tenant,
    command_entry_id,
)
from argos.core.notices import Notice
from argos.core.policy import DocumentLimits, JobPolicy, Policy
from argos.core.ports import (
    Ledger,
    ObjectMetadata,
    ObjectSizeMismatchError,
    ObjectTooLargeError,
    PdfEncryptedError,
    StoredObject,
)
from argos.devtools.bootstrap_bus import declare_topology
from argos.devtools.bootstrap_db import SCHEMA_VERSION, apply_schema
from argos.devtools.bootstrap_store import build_store, ensure_bucket
from argos.platform.bus import JetStreamBus
from argos.platform.ledger import SurrealLedger
from argos.platform.objects import RustFsObjectStore
from argos.platform.ocr import TesseractOcr
from argos.platform.pdf import PdfiumReader
from argos.platform.surreal import JsonValue, SurrealHttp
from argos.services.dispatcher import run_dispatcher
from argos.services.worker import run_worker
from argos.tools.fakes import (
    FakeBus,
    FakeClock,
    InMemoryLedger,
    InMemoryObjectStore,
    RecordingOcr,
    SequentialIds,
    StubPdfReader,
)
from argos.usecases.consumers import (
    ClaimedAttempt,
    ExtractedChunk,
    ExtractionResult,
    Skipped,
    claim_attempt,
    complete_extraction,
    fail_attempt,
)
from argos.usecases.deps import Services
from argos.usecases.dispatch import (
    DispatchReport,
    RecoveryReport,
    dispatch_once,
    outbound_message,
    recover_leases_once,
)
from argos.usecases.documents import (
    DocumentAccepted,
    DocumentRejected,
    DocumentUpload,
    submit_document,
)
from argos.usecases.extract import PdfTools, extract_document
from argos.usecases.notices import NoticeOpened, NoticeRefused, open_notice_case
from argos.usecases.queries import get_case, get_document, get_job

pytestmark = pytest.mark.anyio

type Bus = JetStreamBus | FakeBus
type Store = RustFsObjectStore | InMemoryObjectStore

FIXTURES = Path(__file__).parent / "fixtures"
PDF_BYTES = (FIXTURES / "synthetic_one_page.pdf").read_bytes()
LEDGER_TABLES = {
    "tenant",
    "case",
    "artifact",
    "document",
    "job",
    "attempt",
    "outbox_entry",
    "extraction",
    "chunk",
}
TEST_POLICY = Policy(jobs=JobPolicy(max_attempts=2))


@pytest.fixture(scope="session")
async def ledger_schema(anyio_backend: str, settings: Settings) -> None:
    await apply_schema(settings)


@pytest.fixture(params=["surreal", "memory"])
async def ledger(
    request: pytest.FixtureRequest, anyio_backend: str, settings: Settings, ledger_schema: None
) -> AsyncIterator[Ledger]:
    if request.param == "memory":
        yield InMemoryLedger()
        return
    surreal = SurrealLedger(
        url=f"{settings.surreal_ws_url}/rpc",
        namespace=settings.ops_namespace,
        database=settings.ops_database,
        user=settings.surreal_ledger_user,
        password=settings.surreal_ledger_password.get_secret_value(),
    )
    await surreal.connect()
    try:
        yield surreal
    finally:
        await surreal.close()


@pytest.fixture
async def tenant(ledger: Ledger) -> AsyncIterator[Tenant]:
    record = Tenant(id=f"t-{uuid4().hex[:12]}", name="tenant de prueba", active=True, revision=0)
    await ledger.commit([Insert(record)])
    try:
        yield record
    finally:
        await ledger.delete_tenant_data(record.id)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def bus() -> FakeBus:
    return FakeBus()


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
async def rustfs_store(anyio_backend: str, settings: Settings) -> AsyncIterator[RustFsObjectStore]:
    store = build_store(settings)
    await store.connect()
    try:
        await store.ensure_bucket()
        yield store
    finally:
        await store.close()


@pytest.fixture(params=["rustfs", "memory"])
def object_store(request: pytest.FixtureRequest, rustfs_store: RustFsObjectStore) -> Store:
    return rustfs_store if request.param == "rustfs" else InMemoryObjectStore()


def probe_key(name: str = "source.pdf") -> str:
    marker = uuid4().hex[:12]
    return f"tenants/probe-{marker}/cases/c1/documents/d1/{name}"


def build_services(ledger: Ledger, bus: Bus, clock: FakeClock, store: Store) -> Services:
    return Services(
        ledger=ledger,
        object_store=store,
        bus=bus,
        clock=clock,
        ids=SequentialIds(prefix=f"{uuid4().hex[:8]}-"),
        policy=TEST_POLICY,
        bucket="argos-test",
    )


@pytest.fixture
def services(
    ledger: Ledger, clock: FakeClock, bus: FakeBus, store: InMemoryObjectStore
) -> Services:
    return build_services(ledger, bus, clock, store)


@pytest.fixture(params=["jetstream", "fake"])
async def delivery_bus(
    request: pytest.FixtureRequest, anyio_backend: str, settings: Settings
) -> AsyncIterator[Bus]:
    if request.param == "fake":
        yield FakeBus(max_deliveries=TEST_POLICY.jobs.max_deliveries)
        return
    jetstream = JetStreamBus(settings.nats_url, policy=TEST_POLICY.jobs)
    await jetstream.connect()
    try:
        await jetstream.declare()
        await jetstream.purge(JOBS_STREAM, EVENTS_STREAM)
        yield jetstream
    finally:
        await jetstream.close()


@pytest.fixture
def delivery_services(
    ledger: Ledger, clock: FakeClock, delivery_bus: Bus, store: InMemoryObjectStore
) -> Services:
    return build_services(ledger, delivery_bus, clock, store)


async def chunks_of(data: bytes, size: int = 1024) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def upload_of(
    tenant: Tenant,
    *,
    case_id: str | None = None,
    data: bytes = PDF_BYTES,
    filename: str = "aviso.pdf",
    declared_mime: str = "application/pdf",
    size: int | None = None,
) -> DocumentUpload:
    return DocumentUpload(
        tenant_id=tenant.id,
        case_id=case_id,
        filename=filename,
        declared_mime=declared_mime,
        size=len(data) if size is None else size,
        content=chunks_of(data),
        correlation_id=f"corr-{uuid4().hex[:8]}",
    )


async def accepted_submission(services: Services, tenant: Tenant) -> DocumentAccepted:
    result = await submit_document(services, upload_of(tenant))
    assert isinstance(result, DocumentAccepted)
    return result


async def claimed(services: Services, job_id: str, attempt: int, consumer: str) -> ClaimedAttempt:
    outcome = await claim_attempt(services, JobMessage(job_id, attempt), consumer=consumer)
    assert isinstance(outcome, ClaimedAttempt)
    return outcome


def names_in(section: JsonValue | None) -> set[str]:
    return set(section.keys()) if isinstance(section, dict) else set()


async def test_ledger_schema_is_idempotent(settings: Settings) -> None:
    """S02.1 el esquema del libro de trabajos se aplica de forma idempotente."""
    await apply_schema(settings)
    await apply_schema(settings)
    http = SurrealHttp(settings.surreal_url)
    info = await http.sql(
        "INFO FOR DB;", auth=settings.root_auth, ns=settings.ops_namespace, db=settings.ops_database
    )
    database = info[-1].result
    assert isinstance(database, dict)
    assert names_in(database.get("tables")) >= LEDGER_TABLES
    assert settings.surreal_ledger_user in names_in(database.get("users"))
    version = await http.sql(
        "SELECT version FROM schema_version:current;",
        auth=settings.root_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
    )
    rows = version[-1].result
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    assert rows[0].get("version") == SCHEMA_VERSION


async def test_valid_pdf_is_accepted_before_extraction(
    services: Services, tenant: Tenant, store: InMemoryObjectStore, bus: FakeBus, clock: FakeClock
) -> None:
    """S02.2 enviar un PDF válido responde antes de extraer y deja documento, trabajo y outbox consistentes."""
    result = await accepted_submission(services, tenant)
    assert result.job_state is JobState.QUEUED and not result.reused

    case = await services.ledger.case(result.case_id)
    assert case is not None and case.state is CaseState.AWAITING_PROCESSING
    document = await services.ledger.document(result.document_id)
    assert document is not None
    assert document.state is DocumentState.ACCEPTED
    assert document.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()
    assert document.size == len(PDF_BYTES)
    artifact = await services.ledger.artifact(document.artifact_id)
    assert artifact is not None and artifact.state is ArtifactState.AVAILABLE
    assert artifact.key == source_document_key(tenant.id, result.case_id, result.document_id)
    assert await store.read(artifact.key, limit=len(PDF_BYTES)) == PDF_BYTES

    job = await services.ledger.job(result.job_id)
    assert job is not None
    assert (job.type, job.state, job.attempt, job.max_attempts) == (
        JobType.DOCUMENT_EXTRACT,
        JobState.QUEUED,
        1,
        TEST_POLICY.jobs.max_attempts,
    )
    entries = await services.ledger.outbox_of_job(job.id)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind is OutboxKind.COMMAND and entry.state is OutboxState.PENDING
    assert entry.subject == JOB_SUBJECTS[JobType.DOCUMENT_EXTRACT]
    assert (entry.message_id, entry.attempt, entry.not_before) == (f"{job.id}:1", 1, clock.now())
    assert bus.published == []


async def test_same_pdf_same_case_reuses_and_other_case_does_not(
    services: Services, tenant: Tenant
) -> None:
    """S02.3 el mismo PDF en el mismo caso devuelve lo existente; en otro caso es otro documento."""
    first = await accepted_submission(services, tenant)
    again = await submit_document(services, upload_of(tenant, case_id=first.case_id))
    assert isinstance(again, DocumentAccepted)
    assert again.reused
    assert (again.case_id, again.document_id, again.job_id) == (
        first.case_id,
        first.document_id,
        first.job_id,
    )
    assert len(await services.ledger.jobs_of_case(first.case_id)) == 1

    other = await accepted_submission(services, tenant)
    assert other.case_id != first.case_id
    assert other.document_id != first.document_id and other.job_id != first.job_id
    assert not other.reused


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"data": b"no soy un pdf" * 10}, "document.not_pdf"),
        ({"filename": "aviso.docx"}, "document.bad_extension"),
        ({"declared_mime": "text/plain"}, "document.bad_mime"),
        ({"size": TEST_POLICY.documents.max_bytes + 1}, "document.too_large"),
    ],
)
async def test_cheap_validation_rejects_before_queueing(
    services: Services,
    tenant: Tenant,
    store: InMemoryObjectStore,
    changes: dict[str, bytes | str | int],
    code: str,
) -> None:
    """S02.4 un PDF que no supera la validación barata se rechaza antes de encolar con código estable."""
    data = changes.get("data", PDF_BYTES)
    filename = changes.get("filename", "aviso.pdf")
    declared_mime = changes.get("declared_mime", "application/pdf")
    size = changes.get("size")
    assert isinstance(data, bytes) and isinstance(filename, str) and isinstance(declared_mime, str)
    assert size is None or isinstance(size, int)
    result = await submit_document(
        services,
        upload_of(tenant, data=data, filename=filename, declared_mime=declared_mime, size=size),
    )
    assert result == DocumentRejected(code)
    assert store.objects == {}


async def test_publication_failure_keeps_the_command(
    services: Services, tenant: Tenant, bus: FakeBus
) -> None:
    """S02.5 un fallo entre la transacción y la publicación no pierde el trabajo: el dispatcher lo entrega al recuperarse."""
    submitted = await accepted_submission(services, tenant)
    entry_id = command_entry_id(submitted.job_id, 1)
    bus.failures_remaining = 1

    first = await dispatch_once(services.dispatching)
    assert first.failed == (entry_id,) and first.published == ()
    pending = await services.ledger.outbox_entry(entry_id)
    assert pending is not None
    assert pending.state is OutboxState.PENDING and pending.lease_until is None
    assert bus.published == []

    second = await dispatch_once(services.dispatching)
    assert second.published == (entry_id,)
    published = await services.ledger.outbox_entry(entry_id)
    assert published is not None and published.state is OutboxState.PUBLISHED
    assert len(bus.published) == 1
    message = bus.published[0]
    assert message.subject == JOB_SUBJECTS[JobType.DOCUMENT_EXTRACT]
    assert message.headers[MESSAGE_ID_HEADER] == f"{submitted.job_id}:1"
    assert decode_job_message(message.payload) == JobMessage(submitted.job_id, 1)
    assert set(json.loads(message.payload)) == {"job_id", "attempt"}
    assert await dispatch_once(services.dispatching) == DispatchReport(
        published=(), failed=(), skipped=()
    )


async def test_two_deliveries_of_one_attempt_claim_once(
    services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.6 dos entregas del mismo intento producen una sola reclamación efectiva."""
    submitted = await accepted_submission(services, tenant)
    first = await claim_attempt(services, JobMessage(submitted.job_id, 1), consumer="worker-a")
    assert isinstance(first, ClaimedAttempt)
    second = await claim_attempt(services, JobMessage(submitted.job_id, 1), consumer="worker-b")
    assert isinstance(second, Skipped)

    job = await services.ledger.job(submitted.job_id)
    assert job is not None
    assert job.state is JobState.RUNNING
    assert job.lease_until == clock.now() + TEST_POLICY.jobs.lease
    attempts = await services.ledger.attempts(job.id)
    assert [(a.number, a.consumer, a.state) for a in attempts] == [
        (1, "worker-a", AttemptState.RUNNING)
    ]
    stale = await claim_attempt(services, JobMessage(submitted.job_id, 2), consumer="worker-c")
    assert isinstance(stale, Skipped)


async def test_expired_lease_requeues_then_exhausts(
    services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.7 un intento cuyo arrendamiento vence se reencola con intento nuevo o termina failed al agotar el presupuesto."""
    submitted = await accepted_submission(services, tenant)
    assert (await dispatch_once(services.dispatching)).published == (
        command_entry_id(submitted.job_id, 1),
    )
    await claimed(services, submitted.job_id, 1, "worker-a")

    assert await recover_leases_once(services.dispatching) == RecoveryReport(
        requeued=(), failed=(), skipped=()
    )
    clock.advance(TEST_POLICY.jobs.lease + timedelta(seconds=1))
    recovered = await recover_leases_once(services.dispatching)
    assert recovered.requeued == (submitted.job_id,)

    job = await services.ledger.job(submitted.job_id)
    assert job is not None
    assert (job.state, job.attempt, job.lease_until) == (JobState.QUEUED, 2, None)
    attempts = await services.ledger.attempts(job.id)
    assert [(a.number, a.state, a.error_code) for a in attempts] == [
        (1, AttemptState.LOST, LEASE_LOST)
    ]
    retry = await services.ledger.outbox_entry(command_entry_id(job.id, 2))
    assert retry is not None
    assert retry.state is OutboxState.PENDING
    assert retry.not_before == clock.now() + TEST_POLICY.jobs.backoff(1)
    assert await services.ledger.pending_outbox(clock.now(), limit=10) == []
    assert retry in await services.ledger.pending_outbox(retry.not_before, limit=10)

    await claimed(services, submitted.job_id, 2, "worker-b")
    clock.advance(TEST_POLICY.jobs.lease + timedelta(seconds=1))
    exhausted = await recover_leases_once(services.dispatching)
    assert exhausted.failed == (submitted.job_id,)
    job = await services.ledger.job(submitted.job_id)
    assert job is not None
    assert (job.state, job.public_error, job.lease_until) == (
        JobState.FAILED,
        ATTEMPTS_EXHAUSTED,
        None,
    )
    subjects = [(e.kind, e.subject, e.state) for e in await services.ledger.outbox_of_job(job.id)]
    assert (OutboxKind.EVENT, DOCUMENT_FAILED_SUBJECT, OutboxState.PENDING) in subjects
    assert command_entry_id(job.id, 3) not in [
        e.id for e in await services.ledger.outbox_of_job(job.id)
    ]


async def test_transient_failure_retries_and_permanent_does_not(
    services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.8 un fallo transitorio reintenta con backoff y uno permanente termina failed sin reintentos."""
    submitted = await accepted_submission(services, tenant)
    await claimed(services, submitted.job_id, 1, "worker-a")
    transient = await fail_attempt(
        services,
        job_id=submitted.job_id,
        attempt_number=1,
        kind=FailureKind.TRANSIENT,
        code="worker.dependency_unavailable",
    )
    assert not isinstance(transient, Skipped)
    assert (transient.state, transient.attempt) == (JobState.QUEUED, 2)
    retry = await services.ledger.outbox_entry(command_entry_id(submitted.job_id, 2))
    assert retry is not None and retry.not_before == clock.now() + TEST_POLICY.jobs.backoff(1)

    await claimed(services, submitted.job_id, 2, "worker-b")
    permanent = await fail_attempt(
        services,
        job_id=submitted.job_id,
        attempt_number=2,
        kind=FailureKind.PERMANENT,
        code="pdf.encrypted",
    )
    assert not isinstance(permanent, Skipped)
    assert (permanent.state, permanent.public_error) == (JobState.FAILED, "pdf.encrypted")
    document = await services.ledger.document(submitted.document_id)
    assert document is not None and document.state is DocumentState.REJECTED
    attempts = await services.ledger.attempts(submitted.job_id)
    assert [(a.number, a.state, a.error_kind) for a in attempts] == [
        (1, AttemptState.FAILED, FailureKind.TRANSIENT),
        (2, AttemptState.FAILED, FailureKind.PERMANENT),
    ]
    entries = await services.ledger.outbox_of_job(submitted.job_id)
    assert [e.subject for e in entries if e.kind is OutboxKind.EVENT] == [DOCUMENT_FAILED_SUBJECT]
    assert command_entry_id(submitted.job_id, 3) not in [e.id for e in entries]

    repeated = await fail_attempt(
        services,
        job_id=submitted.job_id,
        attempt_number=2,
        kind=FailureKind.PERMANENT,
        code="pdf.encrypted",
    )
    assert isinstance(repeated, Skipped)


def extraction_result() -> ExtractionResult:
    text = "Aviso sintetico de prueba"
    return ExtractionResult(
        extraction_id="extraction-under-test",
        text_object=StoredObject(key="text", sha256="a" * 64, size=120),
        manifest_object=StoredObject(key="manifest", sha256="b" * 64, size=80),
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        page_count=1,
        ocr_pages=0,
        chunks=(
            ExtractedChunk(
                page=1, position=0, text=text, sha256=hashlib.sha256(text.encode()).hexdigest()
            ),
        ),
    )


async def test_extraction_closes_with_its_event_and_is_idempotent(
    services: Services, tenant: Tenant
) -> None:
    """S02.9 el cierre de una extracción y su evento nacen en una transacción y una segunda confirmación no duplica nada."""
    submitted = await accepted_submission(services, tenant)
    await claimed(services, submitted.job_id, 1, "worker-a")
    completed = await complete_extraction(
        services, job_id=submitted.job_id, attempt_number=1, result=extraction_result()
    )
    assert not isinstance(completed, Skipped)
    assert completed.state is JobState.COMPLETED and completed.lease_until is None

    attempts = await services.ledger.attempts(submitted.job_id)
    assert [(a.number, a.state) for a in attempts] == [(1, AttemptState.SUCCEEDED)]
    document = await services.ledger.document(submitted.document_id)
    assert document is not None and document.page_count == 1
    entries = await services.ledger.outbox_of_job(submitted.job_id)
    events = [e for e in entries if e.kind is OutboxKind.EVENT]
    assert [(e.subject, e.state, e.attempt) for e in events] == [
        (DOCUMENT_EXTRACTED_SUBJECT, OutboxState.PENDING, 1)
    ]
    extractions = await services.ledger.extractions_of_document(submitted.document_id)
    assert [(e.state, e.page_count) for e in extractions] == [(ExtractionState.AVAILABLE, 1)]
    chunks = await services.ledger.chunks(extractions[0].id)
    assert [(c.page, c.position, c.text) for c in chunks] == [(1, 0, "Aviso sintetico de prueba")]

    again = await complete_extraction(
        services, job_id=submitted.job_id, attempt_number=1, result=extraction_result()
    )
    assert isinstance(again, Skipped)
    assert len(await services.ledger.outbox_of_job(submitted.job_id)) == len(entries)
    assert len(await services.ledger.extractions_of_document(submitted.document_id)) == 1


async def test_other_tenant_cannot_see_the_document(services: Services, tenant: Tenant) -> None:
    """S02.10 un documento de otro tenant nunca se puede consultar: caso, trabajo y documento responden como inexistentes."""
    submitted = await accepted_submission(services, tenant)
    stranger = f"t-{uuid4().hex[:12]}"
    assert await get_job(services, tenant_id=stranger, job_id=submitted.job_id) is None
    assert await get_case(services, tenant_id=stranger, case_id=submitted.case_id) is None
    assert (
        await get_document(services, tenant_id=stranger, document_id=submitted.document_id) is None
    )
    own = await get_job(services, tenant_id=tenant.id, job_id=submitted.job_id)
    assert own is not None and own.state is JobState.QUEUED and own.public_error is None
    assert "internal_error" not in own.__dataclass_fields__


async def test_notice_opens_case_and_analysis_job_atomically(
    services: Services, tenant: Tenant, ledger: Ledger
) -> None:
    """S02.11 analyze_notice deja caso y trabajo case.analyze en una transacción, aplica R1 y deduplica por R9."""
    notice = Notice(
        text="Invierte hoy con Nexolabs Capital y dobla tu dinero", links=("https://example.test",)
    )
    opened = await open_notice_case(
        services, tenant_id=tenant.id, notice=notice, correlation_id="c1"
    )
    assert isinstance(opened, NoticeOpened) and not opened.reused
    case = await ledger.case(opened.case_id)
    assert case is not None and case.state is CaseState.RECEIVED and case.notice_hash
    job = await ledger.job(opened.job_id)
    assert job is not None and (job.type, job.state) == (JobType.CASE_ANALYZE, JobState.QUEUED)
    entries = await ledger.outbox_of_job(job.id)
    assert [(e.subject, e.state) for e in entries] == [
        (JOB_SUBJECTS[JobType.CASE_ANALYZE], OutboxState.PENDING)
    ]

    repeated = await open_notice_case(
        services,
        tenant_id=tenant.id,
        notice=replace(notice, text="  invierte HOY con nexolabs capital y dobla tu dinero "),
        correlation_id="c2",
    )
    assert repeated == NoticeOpened(case_id=opened.case_id, job_id=opened.job_id, reused=True)
    assert len(await ledger.jobs_of_case(opened.case_id)) == 1

    other = Tenant(id=f"t-{uuid4().hex[:12]}", name="otro", active=True, revision=0)
    await ledger.commit([Insert(other)])
    try:
        elsewhere = await open_notice_case(
            services, tenant_id=other.id, notice=notice, correlation_id="c3"
        )
        assert isinstance(elsewhere, NoticeOpened) and elsewhere.case_id != opened.case_id
    finally:
        await ledger.delete_tenant_data(other.id)

    too_long = replace(notice, text="x" * (TEST_POLICY.notices.max_text_chars + 1))
    assert await open_notice_case(
        services, tenant_id=tenant.id, notice=too_long, correlation_id="c4"
    ) == NoticeRefused("notice.text_too_long")
    assert await open_notice_case(
        services, tenant_id=tenant.id, notice=Notice(text="   "), correlation_id="c5"
    ) == NoticeRefused("notice.empty")


async def test_bus_topology_is_declared_idempotently(settings: Settings) -> None:
    """S02.12 los streams y los consumidores durables de JetStream se declaran de forma idempotente."""
    policy = Policy()
    await declare_topology(settings, policy)
    state = await declare_topology(settings, policy)

    assert {stream.name for stream in state.streams} == {JOBS_STREAM, EVENTS_STREAM}
    jobs = next(stream for stream in state.streams if stream.name == JOBS_STREAM)
    events = next(stream for stream in state.streams if stream.name == EVENTS_STREAM)
    assert jobs.subjects == frozenset(JOB_SUBJECTS.values())
    assert jobs.workqueue
    assert events.subjects == frozenset(
        {DOCUMENT_EXTRACTED_SUBJECT, DOCUMENT_FAILED_SUBJECT, CASE_COMPLETED_SUBJECT}
    )
    assert not events.workqueue
    for stream in state.streams:
        assert stream.duplicate_window > policy.jobs.outbox_lease

    declared = {consumer.durable: consumer for consumer in state.consumers}
    assert set(declared) == {spec.durable for spec in CONSUMERS}
    for spec in CONSUMERS:
        consumer = declared[spec.durable]
        assert consumer.stream == spec.stream
        assert consumer.subjects == frozenset(spec.subjects)
        assert consumer.explicit_ack
        assert consumer.ack_wait == policy.jobs.lease
        assert consumer.max_deliveries == policy.jobs.max_deliveries


async def test_command_reaches_the_durable_consumer(
    delivery_services: Services, delivery_bus: Bus, tenant: Tenant
) -> None:
    """S02.13 el comando confirmado llega al consumidor durable con solo job_id y attempt."""
    submitted = await accepted_submission(delivery_services, tenant)
    entry_id = command_entry_id(submitted.job_id, 1)
    assert (await dispatch_once(delivery_services.dispatching)).published == (entry_id,)

    source = await delivery_bus.deliveries(DOCUMENT_EXTRACTOR)
    delivered = await source.fetch(limit=10, timeout=2.0)
    assert len(delivered) == 1
    delivery = delivered[0]
    assert delivery.message == JobMessage(submitted.job_id, 1)
    assert delivery.subject == JOB_SUBJECTS[JobType.DOCUMENT_EXTRACT]
    assert delivery.delivery_count == 1

    claimed_attempt = await claim_attempt(
        delivery_services, delivery.message, consumer=DOCUMENT_EXTRACTOR.durable
    )
    assert isinstance(claimed_attempt, ClaimedAttempt)
    await delivery.ack()

    published = await delivery_services.ledger.outbox_entry(entry_id)
    assert published is not None and published.state is OutboxState.PUBLISHED
    assert await source.fetch(limit=10, timeout=1.0) == []


async def test_republishing_one_attempt_delivers_once(
    delivery_services: Services, delivery_bus: Bus, tenant: Tenant
) -> None:
    """S02.14 publicar dos veces el mismo intento entrega una sola vez."""
    submitted = await accepted_submission(delivery_services, tenant)
    entry_id = command_entry_id(submitted.job_id, 1)
    assert (await dispatch_once(delivery_services.dispatching)).published == (entry_id,)

    entry = await delivery_services.ledger.outbox_entry(entry_id)
    assert entry is not None
    await delivery_bus.publish(outbound_message(entry))

    source = await delivery_bus.deliveries(DOCUMENT_EXTRACTOR)
    delivered = await source.fetch(limit=10, timeout=2.0)
    assert [d.message for d in delivered] == [JobMessage(submitted.job_id, 1)]


async def test_unconfirmed_delivery_repeats_without_effect(
    delivery_services: Services, delivery_bus: Bus, tenant: Tenant
) -> None:
    """S02.15 una entrega que el consumidor no confirma vuelve a entregarse y la segunda no tiene efecto."""
    submitted = await accepted_submission(delivery_services, tenant)
    await dispatch_once(delivery_services.dispatching)
    source = await delivery_bus.deliveries(DOCUMENT_EXTRACTOR)

    first = (await source.fetch(limit=10, timeout=2.0))[0]
    claimed_attempt = await claim_attempt(delivery_services, first.message, consumer="worker-a")
    assert isinstance(claimed_attempt, ClaimedAttempt)
    await first.nak()

    repeated = (await source.fetch(limit=10, timeout=2.0))[0]
    assert repeated.message == first.message
    assert repeated.delivery_count == 2
    assert isinstance(
        await claim_attempt(delivery_services, repeated.message, consumer="worker-b"), Skipped
    )
    await repeated.ack()

    attempts = await delivery_services.ledger.attempts(submitted.job_id)
    assert [(a.number, a.consumer, a.state) for a in attempts] == [
        (1, "worker-a", AttemptState.RUNNING)
    ]
    entries = await delivery_services.ledger.outbox_of_job(submitted.job_id)
    assert [(e.id, e.state) for e in entries] == [
        (command_entry_id(submitted.job_id, 1), OutboxState.PUBLISHED)
    ]


async def test_dispatcher_loop_publishes_and_recovers(
    services: Services, tenant: Tenant, clock: FakeClock, bus: FakeBus
) -> None:
    """S02.16 el bucle del dispatcher publica lo pendiente, reencola arrendamientos vencidos y para cuando se le pide."""
    submitted = await accepted_submission(services, tenant)
    await claimed(services, submitted.job_id, 1, "worker-a")

    interval = TEST_POLICY.jobs.lease.total_seconds() + 1
    naps: list[float] = []

    async def sleep(seconds: float) -> None:
        naps.append(seconds)
        clock.advance(timedelta(seconds=seconds))

    ticks = await run_dispatcher(
        services.dispatching, stop=lambda: len(naps) >= 3, sleep=sleep, interval=interval
    )

    assert naps == [interval, interval, interval]
    assert [t.dispatch.published for t in ticks] == [
        (command_entry_id(submitted.job_id, 1),),
        (),
        (command_entry_id(submitted.job_id, 2),),
    ]
    assert [t.recovery.requeued for t in ticks] == [(), (submitted.job_id,), ()]
    assert [decode_job_message(m.payload) for m in bus.published] == [
        JobMessage(submitted.job_id, 1),
        JobMessage(submitted.job_id, 2),
    ]
    job = await services.ledger.job(submitted.job_id)
    assert job is not None and (job.state, job.attempt) == (JobState.QUEUED, 2)


async def test_artifact_bucket_is_private_and_idempotent(
    settings: Settings, rustfs_store: RustFsObjectStore
) -> None:
    """S02.17 el bucket de artefactos se crea de forma idempotente y no sirve nada sin firma."""
    await ensure_bucket(settings)
    await ensure_bucket(settings)

    key = probe_key()
    payload = b"contenido sintetico"
    await rustfs_store.put(key, chunks_of(payload), size=len(payload), mime="application/pdf")
    base = f"{settings.artifact_endpoint}/{settings.artifact_bucket}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        listing = await client.get(base)
        direct = await client.get(f"{base}/{key}")
    assert (listing.status_code, direct.status_code) == (403, 403)
    assert await rustfs_store.read(key, limit=len(payload)) == payload
    await rustfs_store.delete(key)


async def test_object_is_written_streaming_and_read_back(object_store: Store) -> None:
    """S02.18 un objeto se escribe en flujo con su hash y se relee de forma acotada."""
    payload = b"documento sintetico de prueba " * 64
    key = probe_key()

    stored = await object_store.put(
        key, chunks_of(payload, size=97), size=len(payload), mime="application/pdf"
    )
    assert stored == StoredObject(
        key=key, sha256=hashlib.sha256(payload).hexdigest(), size=len(payload)
    )
    assert await object_store.stat(key) == ObjectMetadata(
        key=key, size=len(payload), mime="application/pdf"
    )
    assert await object_store.read(key, limit=len(payload)) == payload
    with pytest.raises(ObjectTooLargeError):
        await object_store.read(key, limit=len(payload) - 1)

    missing = probe_key("ausente.pdf")
    assert await object_store.stat(missing) is None
    assert await object_store.read(missing, limit=1024) is None
    await object_store.delete(key)


async def test_declared_size_must_match_what_is_streamed(object_store: Store) -> None:
    """S02.19 un tamaño declarado que no coincide con lo subido no deja objeto utilizable."""
    payload = b"cuerpo mas corto de lo declarado"
    key = probe_key()
    with pytest.raises(ObjectSizeMismatchError):
        await object_store.put(
            key, chunks_of(payload), size=len(payload) + 1, mime="application/pdf"
        )
    assert await object_store.stat(key) is None


async def test_exact_delete_removes_only_its_object(object_store: Store) -> None:
    """S02.21 el borrado exacto elimina su objeto, deja intactos los demás y no falla si ya no está."""
    payload = b"artefacto"
    doomed, kept = probe_key("doomed.pdf"), probe_key("kept.pdf")
    for key in (doomed, kept):
        await object_store.put(key, chunks_of(payload), size=len(payload), mime="application/pdf")

    await object_store.delete(doomed)
    assert await object_store.stat(doomed) is None
    assert await object_store.stat(kept) is not None
    await object_store.delete(doomed)
    await object_store.delete(kept)


async def test_presigned_url_serves_only_that_object(
    settings: Settings, rustfs_store: RustFsObjectStore
) -> None:
    """S02.20 una URL firmada breve sirve solo su objeto, caduca y sin firma no se sirve nada."""
    payload = b"solo este objeto"
    key, other = probe_key("wanted.pdf"), probe_key("other.pdf")
    for target in (key, other):
        await rustfs_store.put(
            target, chunks_of(payload), size=len(payload), mime="application/pdf"
        )

    fresh = rustfs_store.presigned_get(key, expires_in=timedelta(minutes=5))
    borrowed = rustfs_store.presigned_get(other, expires_in=timedelta(minutes=5))
    stale_store = build_store(settings, clock=FakeClock(datetime.now(UTC) - timedelta(hours=1)))
    stale = stale_store.presigned_get(key, expires_in=timedelta(seconds=60))

    base = f"{settings.artifact_endpoint}/{settings.artifact_bucket}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        served = await client.get(fresh)
        mismatched = await client.get(f"{base}/{key}?{borrowed.partition('?')[2]}")
        expired = await client.get(stale)
        naked = await client.get(f"{base}/{key}")

    assert served.status_code == 200 and served.content == payload
    assert mismatched.status_code == 403
    assert expired.status_code == 403
    assert naked.status_code == 403
    for target in (key, other):
        await rustfs_store.delete(target)


async def test_intake_writes_the_original_to_the_real_store(
    ledger: Ledger,
    clock: FakeClock,
    bus: FakeBus,
    rustfs_store: RustFsObjectStore,
    tenant: Tenant,
) -> None:
    """S02.22 el ingreso completo deja el original en el almacén real bajo la clave de su caso."""
    services = build_services(ledger, bus, clock, rustfs_store)
    submitted = await accepted_submission(services, tenant)

    document = await ledger.document(submitted.document_id)
    assert document is not None
    artifact = await ledger.artifact(document.artifact_id)
    assert artifact is not None
    assert artifact.key == source_document_key(tenant.id, submitted.case_id, submitted.document_id)
    assert await rustfs_store.stat(artifact.key) == ObjectMetadata(
        key=artifact.key, size=len(PDF_BYTES), mime="application/pdf"
    )
    assert await rustfs_store.read(artifact.key, limit=len(PDF_BYTES)) == PDF_BYTES
    await rustfs_store.delete(artifact.key)


MIXED_PDF = (FIXTURES / "synthetic_mixed_pages.pdf").read_bytes()
DAMAGED_PDF = b"%PDF-1.4\nesto no es un documento valido\n"


def pdf_tools(ocr: RecordingOcr) -> PdfTools:
    return PdfTools(reader=PdfiumReader(), ocr=ocr)


async def claimed_extraction(services: Services, tenant: Tenant, data: bytes) -> ClaimedAttempt:
    result = await submit_document(services, upload_of(tenant, data=data))
    assert isinstance(result, DocumentAccepted)
    return await claimed(services, result.job_id, 1, "worker-a")


def decompressed(payload: bytes) -> str:
    return zstandard.ZstdDecompressor().decompress(payload).decode()


async def test_worker_extracts_embedded_text_and_closes_the_extraction(
    services: Services, tenant: Tenant, store: InMemoryObjectStore
) -> None:
    """S02.23 el worker extrae el texto embebido, sube los derivados y cierra la extracción con su evento."""
    ocr = RecordingOcr(text="no deberia usarse")
    claim = await claimed_extraction(services, tenant, PDF_BYTES)

    finished = await extract_document(
        services, pdf_tools(ocr), job=claim.job, attempt=claim.attempt
    )
    assert not isinstance(finished, Skipped)
    assert finished.state is JobState.COMPLETED
    assert ocr.calls == []

    extractions = await services.ledger.extractions_of_document(claim.job.document_id or "")
    assert len(extractions) == 1
    extraction = extractions[0]
    assert (extraction.state, extraction.page_count, extraction.ocr_pages) == (
        ExtractionState.AVAILABLE,
        1,
        0,
    )
    assert extraction.extractor_version == TEST_POLICY.extractor_version

    chunks = await services.ledger.chunks(extraction.id)
    assert [(c.page, c.position) for c in chunks] == [(1, 0)]
    assert chunks[0].text == "Aviso sintetico de prueba"

    text_artifact = await services.ledger.artifact(extraction.text_artifact_id)
    manifest_artifact = await services.ledger.artifact(extraction.manifest_artifact_id)
    assert text_artifact is not None and manifest_artifact is not None
    assert text_artifact.key == extraction_text_key(tenant.id, claim.job.case_id, extraction.id)
    assert manifest_artifact.key == extraction_manifest_key(
        tenant.id, claim.job.case_id, extraction.id
    )
    stored_text = await store.read(text_artifact.key, limit=1 << 20)
    assert stored_text is not None
    assert decompressed(stored_text) == "Aviso sintetico de prueba"

    stored_manifest = await store.read(manifest_artifact.key, limit=1 << 20)
    assert stored_manifest is not None
    described = json.loads(stored_manifest)
    assert described["page_count"] == 1
    assert described["ocr_pages"] == 0
    assert described["pages"] == [{"number": 1, "source": "embedded", "characters": 25}]
    assert [c["position"] for c in described["chunks"]] == [0]

    events = [
        entry
        for entry in await services.ledger.outbox_of_job(claim.job.id)
        if entry.kind is OutboxKind.EVENT
    ]
    assert [(e.subject, e.state) for e in events] == [
        (DOCUMENT_EXTRACTED_SUBJECT, OutboxState.PENDING)
    ]


async def test_ocr_runs_only_on_pages_without_usable_text(
    services: Services, tenant: Tenant
) -> None:
    """S02.24 solo se aplica OCR a las páginas sin texto utilizable y su texto entra en los chunks."""
    ocr = RecordingOcr(TesseractOcr())
    claim = await claimed_extraction(services, tenant, MIXED_PDF)

    finished = await extract_document(
        services, pdf_tools(ocr), job=claim.job, attempt=claim.attempt
    )
    assert not isinstance(finished, Skipped)
    assert finished.state is JobState.COMPLETED
    assert len(ocr.calls) == 1

    extraction = (await services.ledger.extractions_of_document(claim.job.document_id or ""))[0]
    assert (extraction.page_count, extraction.ocr_pages) == (2, 1)
    chunks = await services.ledger.chunks(extraction.id)
    assert [c.page for c in chunks] == [1, 2]
    assert "texto incrustado" in chunks[0].text
    assert "ESCANEADA" in chunks[1].text.upper()

    document = await services.ledger.document(claim.job.document_id or "")
    assert document is not None and document.page_count == 2


@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("damaged", "pdf.damaged"),
        ("encrypted", "pdf.encrypted"),
        ("too_many_pages", "pdf.too_many_pages"),
        ("hash_mismatch", "document.hash_mismatch"),
    ],
)
async def test_unreadable_document_fails_permanently(
    services: Services,
    tenant: Tenant,
    store: InMemoryObjectStore,
    scenario: str,
    code: str,
) -> None:
    """S02.25 un documento que el worker no puede leer termina en fallo permanente con código estable."""
    payload = MIXED_PDF if scenario == "too_many_pages" else PDF_BYTES
    if scenario == "damaged":
        payload = DAMAGED_PDF
    claim = await claimed_extraction(services, tenant, payload)
    tools = pdf_tools(RecordingOcr())
    running = services
    if scenario == "encrypted":
        tools = PdfTools(
            reader=StubPdfReader(error=PdfEncryptedError("needs a password")),
            ocr=RecordingOcr(),
        )
    if scenario == "too_many_pages":
        running = replace(
            services, policy=replace(TEST_POLICY, documents=DocumentLimits(max_pages=1))
        )
    if scenario == "hash_mismatch":
        document = await services.ledger.document(claim.job.document_id or "")
        assert document is not None
        artifact = await services.ledger.artifact(document.artifact_id)
        assert artifact is not None
        other = b"%PDF-1.4\notros bytes distintos\n"
        await store.put(artifact.key, chunks_of(other), size=len(other), mime="application/pdf")

    finished = await extract_document(running, tools, job=claim.job, attempt=claim.attempt)
    assert not isinstance(finished, Skipped)
    assert (finished.state, finished.public_error) == (JobState.FAILED, code)

    document = await services.ledger.document(claim.job.document_id or "")
    assert document is not None and document.state is DocumentState.REJECTED
    assert await services.ledger.extractions_of_document(document.id) == []
    events = [
        entry
        for entry in await services.ledger.outbox_of_job(claim.job.id)
        if entry.kind is OutboxKind.EVENT
    ]
    assert [e.subject for e in events] == [DOCUMENT_FAILED_SUBJECT]
    attempts = await services.ledger.attempts(claim.job.id)
    assert [(a.number, a.state, a.error_kind) for a in attempts] == [
        (1, AttemptState.FAILED, FailureKind.PERMANENT)
    ]


async def test_worker_loop_extracts_and_acknowledges_every_delivery(
    services: Services, tenant: Tenant, bus: FakeBus
) -> None:
    """S02.26 el bucle del worker extrae lo que reclama, confirma sin trabajar lo que ya no le toca y para cuando se le pide."""
    mine = await submit_document(services, upload_of(tenant))
    taken = await submit_document(services, upload_of(tenant))
    assert isinstance(mine, DocumentAccepted) and isinstance(taken, DocumentAccepted)
    await dispatch_once(services.dispatching)
    await claimed(services, taken.job_id, 1, "otro-worker")

    source = await bus.deliveries(DOCUMENT_EXTRACTOR)
    naps: list[float] = []

    async def sleep(seconds: float) -> None:
        naps.append(seconds)

    ticks = await run_worker(
        services,
        pdf_tools(RecordingOcr()),
        source,
        consumer="worker-a",
        stop=lambda: len(naps) >= 1,
        sleep=sleep,
        interval=0.1,
    )

    assert naps == [0.1]
    assert [tick.extracted for tick in ticks] == [(mine.job_id,)]
    assert [tick.skipped for tick in ticks] == [(taken.job_id,)]
    extracted = await services.ledger.job(mine.job_id)
    untouched = await services.ledger.job(taken.job_id)
    assert extracted is not None and extracted.state is JobState.COMPLETED
    assert untouched is not None and untouched.state is JobState.RUNNING
    assert await source.fetch(limit=10, timeout=0.1) == []
