"""Casos S02.1 a S02.11: libro de trabajos, outbox e ingreso de documentos y avisos."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from argos.config import Settings
from argos.core.keys import source_document_key
from argos.core.ledger import ATTEMPTS_EXHAUSTED, LEASE_LOST
from argos.core.messages import (
    DOCUMENT_EXTRACTED_SUBJECT,
    DOCUMENT_FAILED_SUBJECT,
    JOB_SUBJECTS,
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
from argos.core.policy import JobPolicy, Policy
from argos.core.ports import Ledger, StoredObject
from argos.devtools.bootstrap_db import SCHEMA_VERSION, apply_schema
from argos.platform.ledger import SurrealLedger
from argos.platform.surreal import JsonValue, SurrealHttp
from argos.tools.fakes import FakeBus, FakeClock, InMemoryLedger, InMemoryObjectStore, SequentialIds
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
    recover_leases_once,
)
from argos.usecases.documents import (
    DocumentAccepted,
    DocumentRejected,
    DocumentUpload,
    submit_document,
)
from argos.usecases.notices import NoticeOpened, NoticeRefused, open_notice_case
from argos.usecases.queries import get_case, get_document, get_job

pytestmark = pytest.mark.anyio

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
def services(
    ledger: Ledger, clock: FakeClock, bus: FakeBus, store: InMemoryObjectStore
) -> Services:
    return Services(
        ledger=ledger,
        object_store=store,
        bus=bus,
        clock=clock,
        ids=SequentialIds(prefix=f"{uuid4().hex[:8]}-"),
        policy=TEST_POLICY,
        bucket="argos-test",
    )


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
    assert store.objects[artifact.key] == PDF_BYTES

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

    first = await dispatch_once(services)
    assert first.failed == (entry_id,) and first.published == ()
    pending = await services.ledger.outbox_entry(entry_id)
    assert pending is not None
    assert pending.state is OutboxState.PENDING and pending.lease_until is None
    assert bus.published == []

    second = await dispatch_once(services)
    assert second.published == (entry_id,)
    published = await services.ledger.outbox_entry(entry_id)
    assert published is not None and published.state is OutboxState.PUBLISHED
    assert len(bus.published) == 1
    message = bus.published[0]
    assert message.subject == JOB_SUBJECTS[JobType.DOCUMENT_EXTRACT]
    assert message.headers[MESSAGE_ID_HEADER] == f"{submitted.job_id}:1"
    assert decode_job_message(message.payload) == JobMessage(submitted.job_id, 1)
    assert set(json.loads(message.payload)) == {"job_id", "attempt"}
    assert await dispatch_once(services) == DispatchReport(published=(), failed=(), skipped=())


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
    assert (await dispatch_once(services)).published == (command_entry_id(submitted.job_id, 1),)
    await claimed(services, submitted.job_id, 1, "worker-a")

    assert await recover_leases_once(services) == RecoveryReport(
        requeued=(), failed=(), skipped=()
    )
    clock.advance(TEST_POLICY.jobs.lease + timedelta(seconds=1))
    recovered = await recover_leases_once(services)
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
    exhausted = await recover_leases_once(services)
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
