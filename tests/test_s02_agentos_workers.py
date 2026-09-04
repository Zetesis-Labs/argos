"""Casos S02: libro, workers, agentes, gateway y datos sintéticos de demostración."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncIterable, AsyncIterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import zstandard
from agno.os import AgentOS
from fastapi import FastAPI
from fastapi.routing import APIRoute
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from argos.agents.cluster import ANALYSIS_OF_AGENT, TEAM_NAME, build_cluster
from argos.agents.tools import dumps, history_payload, manifest_payload, tools_for
from argos.api.gateway import Gateway, build_app, job_payload
from argos.config import WORKLOADS, Settings
from argos.core.agents import INVESTIGATION_TEAM, AgentName, Capability, capabilities_of
from argos.core.analysis import (
    ACTIONS,
    DraftEntity,
    DraftSignal,
    EntityHistory,
    Evidence,
    assess,
    score,
    usable,
)
from argos.core.capabilities import CARD_PATH, GATEWAY_CAPABILITIES, MESSAGES_PATH
from argos.core.identity import Identity, Role
from argos.core.keys import extraction_manifest_key, extraction_text_key, source_document_key
from argos.core.knowledge import parse_knowledge_bundle, warnings_from_bundle
from argos.core.ledger import ATTEMPTS_EXHAUSTED, LEASE_LOST, new_job
from argos.core.messages import (
    CASE_ANALYZER,
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
    TERMINAL_CASE_STATES,
    Analysis,
    Artifact,
    ArtifactState,
    AttemptState,
    Case,
    CaseEntity,
    CaseState,
    Chunk,
    Document,
    DocumentState,
    Entity,
    EntityKind,
    Extraction,
    ExtractionState,
    FailureKind,
    Insert,
    Job,
    JobState,
    JobType,
    OutboxKind,
    OutboxState,
    ReviewState,
    RiskLevel,
    Strength,
    Tenant,
    VerdictOutcome,
    analysis_job_id,
    case_entity_id,
    command_entry_id,
    entity_id,
)
from argos.core.notices import Notice
from argos.core.observability import INTERNAL_ERROR, public_code
from argos.core.policy import AnalysisPolicy, DocumentLimits, JobPolicy, Policy
from argos.core.ports import (
    Clock,
    ConversationBrief,
    Investigation,
    Ledger,
    ObjectMetadata,
    ObjectSizeMismatchError,
    ObjectStoreError,
    ObjectTooLargeError,
    PdfEncryptedError,
    StoredObject,
)
from argos.core.reports import NO_VERDICT_YET, investigation_prompt
from argos.core.reprocess import reprocess_options
from argos.devtools.bootstrap_bus import declare_topology
from argos.devtools.bootstrap_db import SCHEMA_VERSION, apply_schema
from argos.devtools.bootstrap_store import build_store, ensure_bucket
from argos.devtools.project_knowledge import read_bundle
from argos.devtools.rehearse_store import rehearse
from argos.platform.agno_db import build_agno_db
from argos.platform.bus import JetStreamBus
from argos.platform.ledger import ledger_for
from argos.platform.objects import RustFsObjectStore
from argos.platform.ocr import TesseractOcr
from argos.platform.pdf import PdfiumReader
from argos.platform.surreal import SurrealError, SurrealHttp
from argos.services.dispatcher import run_dispatcher
from argos.services.worker import run_worker
from argos.tools.fakes import (
    FakeBus,
    FakeClock,
    InMemoryLedger,
    InMemoryObjectStore,
    RecordingOcr,
    ScriptedInvestigator,
    ScriptedNarrator,
    SequentialIds,
    StubPdfReader,
)
from argos.usecases.analysis import Analyzed, analyze_case, build_brief
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
from argos.usecases.janitor import enforce_retention, sweep_staging
from argos.usecases.notices import NoticeOpened, NoticeRefused, open_notice_case
from argos.usecases.queries import get_case, get_document, get_job
from argos.usecases.resume import AnalysisQueued, resume_case
from argos.usecases.tools import (
    CASE_NOT_FOUND,
    EXTRACTION_NOT_FOUND,
    NOT_AUTHORIZED,
    ChunkPage,
    ManifestView,
    ToolCaller,
    ToolDenied,
    find_entity_history,
    find_registry_matches,
    get_extraction_chunks,
    get_extraction_manifest,
)
from tests.support import names_in, wait_for_observations

pytestmark = pytest.mark.anyio

type Bus = JetStreamBus | FakeBus
type Store = RustFsObjectStore | InMemoryObjectStore

FIXTURES = Path(__file__).parent / "fixtures"
PDF_BYTES = (FIXTURES / "synthetic_one_page.pdf").read_bytes()
OTHER_PDF = (FIXTURES / "synthetic_mixed_pages.pdf").read_bytes()
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
ANALYSES = tuple(ANALYSIS_OF_AGENT[member] for member in INVESTIGATION_TEAM)
MEMORY_TABLES = {"entity", "entity_link", "case_entity", "warning", "signal", "verdict"}
SHARED_TABLES = {"entity", "entity_link", "warning"}
TEST_POLICY = Policy(
    jobs=JobPolicy(max_attempts=2),
    analysis=AnalysisPolicy(chunk_budget=2, budget=timedelta(seconds=1)),
)


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
    surreal = ledger_for(settings, "worker")
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
    assert set(WORKLOADS) <= names_in(database.get("users"))
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


async def reserved_derivatives(services: Services, job: Job, extraction_id: str) -> tuple[str, str]:
    """Los derivados nacen como referencia `uploading`, igual que en el ingreso."""
    now = services.clock.now()
    reserved: list[Artifact] = []
    for key, mime in (
        (extraction_text_key(job.tenant_id, job.case_id, extraction_id), "application/zstd"),
        (extraction_manifest_key(job.tenant_id, job.case_id, extraction_id), "application/json"),
    ):
        artifact = Artifact(
            id=services.ids.new_id(),
            tenant_id=job.tenant_id,
            case_id=job.case_id,
            bucket=services.bucket,
            key=key,
            state=ArtifactState.UPLOADING,
            sha256=None,
            size=0,
            mime=mime,
            created_at=now,
            expires_at=now + TEST_POLICY.retention.staging,
            revision=0,
        )
        reserved.append(artifact)
    await services.ledger.commit([Insert(artifact) for artifact in reserved])
    return reserved[0].id, reserved[1].id


async def extraction_result(services: Services, job: Job) -> ExtractionResult:
    text = "Aviso sintetico de prueba"
    extraction_id = services.ids.new_id()
    text_id, manifest_id = await reserved_derivatives(services, job, extraction_id)
    digest = hashlib.sha256(text.encode()).hexdigest()
    return ExtractionResult(
        extraction_id=extraction_id,
        text_artifact_id=text_id,
        manifest_artifact_id=manifest_id,
        text_object=StoredObject(
            key=extraction_text_key(job.tenant_id, job.case_id, extraction_id),
            sha256="a" * 64,
            size=120,
        ),
        manifest_object=StoredObject(
            key=extraction_manifest_key(job.tenant_id, job.case_id, extraction_id),
            sha256="b" * 64,
            size=80,
        ),
        sha256=digest,
        page_count=1,
        ocr_pages=0,
        chunks=(ExtractedChunk(page=1, position=0, text=text, sha256=digest),),
    )


async def test_extraction_closes_with_its_event_and_is_idempotent(
    services: Services, tenant: Tenant
) -> None:
    """S02.9 el cierre de una extracción y su evento nacen en una transacción y una segunda confirmación no duplica nada."""
    submitted = await accepted_submission(services, tenant)
    working = await claimed(services, submitted.job_id, 1, "worker-a")
    completed = await complete_extraction(
        services,
        job_id=submitted.job_id,
        attempt_number=1,
        result=await extraction_result(services, working.job),
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
        services,
        job_id=submitted.job_id,
        attempt_number=1,
        result=await extraction_result(services, working.job),
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


@dataclass(frozen=True)
class SeededCase:
    case_id: str
    document_id: str
    extraction_id: str
    chunk_ids: tuple[str, ...]


def evidence_of(clock: Clock, quote: str = "cita del fragmento") -> Evidence:
    return Evidence(
        source="registro oficial", observed_at=clock.now(), value="ejemplo.test", quote=quote
    )


def signal_of(
    clock: Clock,
    analysis: Analysis,
    strength: Strength = Strength.STRONG,
    *,
    official: bool = False,
    recidivism: bool = False,
    evidence: Evidence | None = None,
) -> DraftSignal:
    return DraftSignal(
        analysis=analysis,
        code=f"{analysis}.indicio",
        strength=strength,
        evidence=evidence or evidence_of(clock),
        official=official,
        recidivism=recidivism,
    )


async def seed_case(
    services: Services,
    tenant: Tenant,
    *,
    state: CaseState = CaseState.AWAITING_PROCESSING,
    review: ReviewState = ReviewState.UNREVIEWED,
) -> Case:
    now = services.clock.now()
    case = Case(
        id=services.ids.new_id(),
        tenant_id=tenant.id,
        state=state,
        notice_hash=None,
        language="es",
        correlation_id=f"corr-{uuid4().hex[:8]}",
        previous_case_id=None,
        review_state=review,
        reviewed_at=None,
        reviewed_by=None,
        created_at=now,
        updated_at=now,
        revision=0,
    )
    await services.ledger.commit([Insert(case)])
    return case


async def seed_extraction(
    services: Services, tenant: Tenant, case: Case, *, texts: Sequence[str]
) -> SeededCase:
    now = services.clock.now()
    expires = now + TEST_POLICY.retention.full_content
    document = Document(
        id=services.ids.new_id(),
        tenant_id=tenant.id,
        case_id=case.id,
        artifact_id=services.ids.new_id(),
        sha256=hashlib.sha256(case.id.encode()).hexdigest(),
        mime="application/pdf",
        size=1024,
        page_count=len(texts),
        state=DocumentState.ACCEPTED,
        created_at=now,
        expires_at=expires,
        revision=0,
    )
    extraction = Extraction(
        id=services.ids.new_id(),
        tenant_id=tenant.id,
        case_id=case.id,
        document_id=document.id,
        extractor_version=TEST_POLICY.extractor_version,
        options=TEST_POLICY.extraction_options,
        state=ExtractionState.AVAILABLE,
        sha256=hashlib.sha256(b"extraction").hexdigest(),
        page_count=len(texts),
        ocr_pages=0,
        text_artifact_id=services.ids.new_id(),
        manifest_artifact_id=services.ids.new_id(),
        created_at=now,
        expires_at=expires,
        revision=0,
    )
    chunks = [
        Chunk(
            id=services.ids.new_id(),
            tenant_id=tenant.id,
            extraction_id=extraction.id,
            page=1,
            position=position,
            text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            expires_at=expires,
            revision=0,
        )
        for position, text in enumerate(texts)
    ]
    await services.ledger.commit(
        [Insert(document), Insert(extraction), *(Insert(chunk) for chunk in chunks)]
    )
    return SeededCase(
        case_id=case.id,
        document_id=document.id,
        extraction_id=extraction.id,
        chunk_ids=tuple(chunk.id for chunk in chunks),
    )


async def seed_analysis_job(services: Services, tenant: Tenant, case: Case) -> Job:
    job = new_job(
        job_id=analysis_job_id(case.id, 1),
        tenant_id=tenant.id,
        case_id=case.id,
        job_type=JobType.CASE_ANALYZE,
        document_id=None,
        now=services.clock.now(),
        policy=TEST_POLICY.jobs,
        extractor_version="",
        options="{}",
        correlation_id=case.correlation_id,
    )
    await services.ledger.commit([Insert(job)])
    return job


async def claimed_analysis(services: Services, job: Job) -> ClaimedAttempt:
    claimed = await claim_attempt(
        services, JobMessage(job_id=job.id, attempt=1), consumer=CASE_ANALYZER.durable
    )
    assert isinstance(claimed, ClaimedAttempt)
    return claimed


def caller_of(agent: AgentName, tenant: Tenant, case_id: str) -> ToolCaller:
    return ToolCaller(agent=agent, tenant_id=tenant.id, case_id=case_id)


async def test_memory_schema_is_idempotent(settings: Settings, ledger_schema: None) -> None:
    """S02.27 el esquema de memoria compartida, señales y veredictos se aplica de forma idempotente."""
    await apply_schema(settings)
    await apply_schema(settings)
    http = SurrealHttp(settings.surreal_url)
    info = await http.sql(
        "INFO FOR DB;", auth=settings.root_auth, ns=settings.ops_namespace, db=settings.ops_database
    )
    database = info[-1].result
    assert isinstance(database, dict)
    tables = names_in(database.get("tables"))
    assert tables >= LEDGER_TABLES | MEMORY_TABLES

    async def fields_of(table: str) -> set[str]:
        described = await http.sql(
            f"INFO FOR TABLE {table};",
            auth=settings.root_auth,
            ns=settings.ops_namespace,
            db=settings.ops_database,
        )
        definition = described[-1].result
        assert isinstance(definition, dict)
        return names_in(definition.get("fields"))

    for table in sorted(MEMORY_TABLES):
        assert ("tenant_id" in await fields_of(table)) is (table not in SHARED_TABLES)
    assert "review_state" in await fields_of("case")

    version = await http.sql(
        "SELECT version FROM schema_version:current;",
        auth=settings.root_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
    )
    rows = version[-1].result
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    assert rows[0].get("version") == SCHEMA_VERSION


def test_risk_levels_follow_r4(clock: FakeClock) -> None:
    """S02.28 el núcleo determinista calcula el nivel conforme a R4."""
    strong_registry = signal_of(clock, Analysis.REGISTRIES)
    strong_domain = signal_of(clock, Analysis.DOMAIN)
    weak = signal_of(clock, Analysis.PATTERNS, Strength.WEAK)

    assert score([signal_of(clock, Analysis.REGISTRIES, official=True)], degraded=False) is (
        RiskLevel.CRITICAL
    )
    assert score([signal_of(clock, Analysis.MEMORY, recidivism=True)], degraded=False) is (
        RiskLevel.CRITICAL
    )
    assert score([strong_registry, strong_domain], degraded=False) is RiskLevel.HIGH
    assert score([strong_registry, strong_registry], degraded=False) is RiskLevel.MEDIUM
    assert score([strong_registry], degraded=False) is RiskLevel.MEDIUM
    assert score([weak, weak, weak], degraded=False) is RiskLevel.MEDIUM
    assert score([weak, weak], degraded=False) is RiskLevel.LOW
    assert score([], degraded=False) is RiskLevel.LOW


def test_signals_without_evidence_do_not_score(clock: FakeClock) -> None:
    """S02.29 una señal sin evidencia no puntúa y un parcial nunca es low."""
    complete = evidence_of(clock)
    incomplete = (
        replace(complete, source=" "),
        replace(complete, observed_at=None),
        replace(complete, quote=""),
    )
    unsupported = [signal_of(clock, Analysis.PATTERNS, evidence=broken) for broken in incomplete]
    assert usable(unsupported) == ()

    weak = [signal_of(clock, Analysis.PATTERNS, Strength.WEAK) for _ in range(2)]
    assert assess(weak, missing=(), analyzable=True).level is RiskLevel.LOW
    degraded = assess(weak, missing=("document:d1",), analyzable=True)
    assert degraded.level is RiskLevel.MEDIUM
    assert degraded.outcome is VerdictOutcome.PARTIAL and degraded.missing == ("document:d1",)

    empty = assess(unsupported, missing=("registries",), analyzable=True)
    assert empty.level is RiskLevel.UNDETERMINED and empty.outcome is VerdictOutcome.PARTIAL
    assert all(ACTIONS[level] for level in RiskLevel)
    assert empty.actions


def test_agents_only_declare_read_tools(services: Services, tenant: Tenant) -> None:
    """S02.30 cada agente declara solo sus herramientas y ninguna crea ni reprocesa trabajos."""
    assert set(AgentName) == {
        AgentName.TRIAGE,
        AgentName.REGISTRIES,
        AgentName.DOMAIN,
        AgentName.PATTERNS,
        AgentName.MEMORY,
        AgentName.DOCUMENT,
        AgentName.VERDICT_WRITER,
        AgentName.CONVERSATION,
    }
    forbidden = ("create", "reprocess", "submit", "close", "delete", "write", "query")
    assert not [
        capability
        for capability in Capability
        if any(word in str(capability) for word in forbidden)
    ]
    assert capabilities_of(AgentName.DOCUMENT) == {
        Capability.GET_DOCUMENT_JOB,
        Capability.GET_EXTRACTION_MANIFEST,
        Capability.GET_EXTRACTION_CHUNKS,
    }
    assert Capability.GET_EXTRACTION_CHUNKS not in capabilities_of(AgentName.CONVERSATION)
    assert INVESTIGATION_TEAM == (
        AgentName.TRIAGE,
        AgentName.REGISTRIES,
        AgentName.DOMAIN,
        AgentName.PATTERNS,
        AgentName.MEMORY,
    )
    for agent in AgentName:
        bound = tools_for(services, caller_of(agent, tenant, "case-1"))
        assert {tool.capability for tool in bound} == capabilities_of(agent)


async def test_tools_refuse_foreign_agent_tenant_and_case(
    services: Services, tenant: Tenant
) -> None:
    """S02.31 una herramienta rechaza al agente sin capacidad, a otro tenant y a otro caso."""
    case = await seed_case(services, tenant)
    seeded = await seed_extraction(services, tenant, case, texts=("primer fragmento",))
    stranger = Tenant(id=f"t-{uuid4().hex[:12]}", name="ajeno", active=True, revision=0)
    await services.ledger.commit([Insert(stranger)])
    sibling = await seed_case(services, tenant)

    writer = await get_extraction_chunks(
        services,
        caller_of(AgentName.VERDICT_WRITER, tenant, case.id),
        extraction_id=seeded.extraction_id,
    )
    foreign = await get_extraction_chunks(
        services,
        ToolCaller(agent=AgentName.DOCUMENT, tenant_id=stranger.id, case_id=case.id),
        extraction_id=seeded.extraction_id,
    )
    sibling_case = await get_extraction_chunks(
        services,
        caller_of(AgentName.DOCUMENT, tenant, sibling.id),
        extraction_id=seeded.extraction_id,
    )
    unknown = await get_extraction_manifest(
        services, caller_of(AgentName.DOCUMENT, tenant, case.id), extraction_id="extraction:ghost"
    )
    assert writer == ToolDenied(NOT_AUTHORIZED)
    assert foreign == ToolDenied(CASE_NOT_FOUND)
    assert sibling_case == ToolDenied(EXTRACTION_NOT_FOUND)
    assert unknown == ToolDenied(EXTRACTION_NOT_FOUND)
    await services.ledger.delete_tenant_data(stranger.id)


async def test_chunks_are_served_by_reference_within_budget(
    services: Services, tenant: Tenant
) -> None:
    """S02.32 los fragmentos se entregan por referencia y acotados al presupuesto."""
    texts = ("primer fragmento", "segundo fragmento", "tercer fragmento")
    case = await seed_case(services, tenant)
    seeded = await seed_extraction(services, tenant, case, texts=texts)
    caller = caller_of(AgentName.DOCUMENT, tenant, case.id)

    manifest = await get_extraction_manifest(services, caller, extraction_id=seeded.extraction_id)
    assert isinstance(manifest, ManifestView)
    assert manifest.chunk_ids == seeded.chunk_ids
    assert [page.chunks for page in manifest.pages] == [3]
    rendered = dumps(manifest_payload(manifest))
    assert all(text not in rendered for text in texts)

    first = await get_extraction_chunks(services, caller, extraction_id=seeded.extraction_id)
    assert isinstance(first, ChunkPage)
    assert len(first.chunks) == TEST_POLICY.analysis.chunk_budget
    assert [chunk.text for chunk in first.chunks] == list(texts[:2])
    assert first.cursor == 2
    second = await get_extraction_chunks(
        services, caller, extraction_id=seeded.extraction_id, cursor=first.cursor
    )
    assert isinstance(second, ChunkPage)
    assert [chunk.chunk_id for chunk in second.chunks] == [seeded.chunk_ids[2]]
    assert second.cursor is None


async def test_entity_history_only_returns_aggregates(services: Services, tenant: Tenant) -> None:
    """S02.33 find_entity_history devuelve al otro tenant solo agregados."""
    domain = f"inversiones-{uuid4().hex[:10]}.test"
    identifier = entity_id(EntityKind.DOMAIN, domain)
    now = services.clock.now()
    confirmed = await seed_case(services, tenant, review=ReviewState.CONFIRMED)
    plain = await seed_case(services, tenant)
    await services.ledger.commit(
        [
            Insert(
                Entity(
                    id=identifier,
                    kind=EntityKind.DOMAIN,
                    value=domain,
                    strength=Strength.STRONG,
                    first_seen_at=now,
                    last_seen_at=now,
                    revision=0,
                )
            ),
            *(
                Insert(
                    CaseEntity(
                        id=case_entity_id(case.id, identifier),
                        tenant_id=tenant.id,
                        case_id=case.id,
                        entity_id=identifier,
                        created_at=now,
                        revision=0,
                    )
                )
                for case in (confirmed, plain)
            ),
        ]
    )

    stranger = Tenant(id=f"t-{uuid4().hex[:12]}", name="ajeno", active=True, revision=0)
    await services.ledger.commit([Insert(stranger)])
    other_case = await seed_case(services, stranger)
    history = await find_entity_history(
        services,
        ToolCaller(agent=AgentName.MEMORY, tenant_id=stranger.id, case_id=other_case.id),
        kind=EntityKind.DOMAIN,
        value=f"  {domain.upper()} ",
    )
    assert isinstance(history, EntityHistory)
    assert (history.cases, history.confirmed) == (2, True)
    assert history.first_seen_at is not None and history.last_seen_at is not None
    rendered = dumps(history_payload(history))
    assert confirmed.id not in rendered and plain.id not in rendered and tenant.id not in rendered

    unseen = await find_entity_history(
        services,
        ToolCaller(agent=AgentName.MEMORY, tenant_id=stranger.id, case_id=other_case.id),
        kind=EntityKind.DOMAIN,
        value=f"desconocido-{uuid4().hex[:8]}.test",
    )
    assert isinstance(unseen, EntityHistory)
    assert (unseen.cases, unseen.confirmed, unseen.first_seen_at) == (0, False, None)
    await services.ledger.delete_tenant_data(stranger.id)


@dataclass(frozen=True)
class FailedDocument:
    document_id: str
    job_id: str


async def complete_document_job(services: Services, job_id: str) -> None:
    claimed = await claim_attempt(
        services, JobMessage(job_id=job_id, attempt=1), consumer=DOCUMENT_EXTRACTOR.durable
    )
    assert isinstance(claimed, ClaimedAttempt)
    result = await extraction_result(services, claimed.job)
    done = await complete_extraction(services, job_id=job_id, attempt_number=1, result=result)
    assert not isinstance(done, Skipped)


async def failed_document_job(services: Services, tenant: Tenant, case: Case) -> FailedDocument:
    now = services.clock.now()
    marker = services.ids.new_id()
    document = Document(
        id=marker,
        tenant_id=tenant.id,
        case_id=case.id,
        artifact_id=services.ids.new_id(),
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime="application/pdf",
        size=512,
        page_count=None,
        state=DocumentState.ACCEPTED,
        created_at=now,
        expires_at=now + TEST_POLICY.retention.full_content,
        revision=0,
    )
    job = new_job(
        job_id=services.ids.new_id(),
        tenant_id=tenant.id,
        case_id=case.id,
        job_type=JobType.DOCUMENT_EXTRACT,
        document_id=document.id,
        now=now,
        policy=TEST_POLICY.jobs,
        extractor_version=TEST_POLICY.extractor_version,
        options=TEST_POLICY.extraction_options,
        correlation_id=case.correlation_id,
    )
    await services.ledger.commit([Insert(document), Insert(job)])
    claimed = await claim_attempt(
        services, JobMessage(job_id=job.id, attempt=1), consumer=DOCUMENT_EXTRACTOR.durable
    )
    assert isinstance(claimed, ClaimedAttempt)
    await fail_attempt(
        services,
        job_id=job.id,
        attempt_number=1,
        kind=FailureKind.PERMANENT,
        code=PdfEncryptedError.code,
    )
    return FailedDocument(document_id=document.id, job_id=job.id)


async def test_resumer_queues_analysis_when_no_document_is_pending(
    services: Services, tenant: Tenant
) -> None:
    """S02.34 el resumer crea el trabajo de análisis solo cuando no quedan documentos pendientes."""
    first = await accepted_submission(services, tenant)
    second = await submit_document(
        services, upload_of(tenant, case_id=first.case_id, data=OTHER_PDF)
    )
    assert isinstance(second, DocumentAccepted)

    early = await resume_case(services, JobMessage(job_id=first.job_id, attempt=1))
    assert isinstance(early, Skipped)
    assert not [
        job
        for job in await services.ledger.jobs_of_case(first.case_id)
        if job.type is JobType.CASE_ANALYZE
    ]

    for job_id in (first.job_id, second.job_id):
        await complete_document_job(services, job_id)
    queued = await resume_case(services, JobMessage(job_id=second.job_id, attempt=1))
    assert isinstance(queued, AnalysisQueued)
    analysis = await services.ledger.job(queued.job_id)
    assert analysis is not None
    assert (analysis.type, analysis.state) == (JobType.CASE_ANALYZE, JobState.QUEUED)
    entries = await services.ledger.outbox_of_job(analysis.id)
    assert [(entry.subject, entry.state) for entry in entries] == [
        (JOB_SUBJECTS[JobType.CASE_ANALYZE], OutboxState.PENDING)
    ]

    repeated = await resume_case(services, JobMessage(job_id=second.job_id, attempt=1))
    assert isinstance(repeated, Skipped)
    assert [
        job
        for job in await services.ledger.jobs_of_case(first.case_id)
        if job.type is JobType.CASE_ANALYZE
    ] == [analysis]
    case = await services.ledger.case(first.case_id)
    assert case is not None and case.state is CaseState.AWAITING_PROCESSING


async def test_analyzer_issues_a_verdict_once(services: Services, tenant: Tenant) -> None:
    """S02.35 el case analyzer reclama el intento, pasa el caso a analyzing y emite el veredicto."""
    case = await seed_case(services, tenant)
    await seed_extraction(services, tenant, case, texts=("promesa de rentabilidad garantizada",))
    job = await seed_analysis_job(services, tenant, case)
    claimed = await claimed_analysis(services, job)

    domain = f"inversiones-{uuid4().hex[:10]}.test"
    investigator = ScriptedInvestigator(
        Investigation(
            signals=(
                signal_of(services.clock, Analysis.REGISTRIES),
                signal_of(services.clock, Analysis.DOMAIN),
            ),
            entities=(DraftEntity(kind=EntityKind.DOMAIN, value=domain, strength=Strength.STRONG),),
            missing=(),
        )
    )
    narrator = ScriptedNarrator("Hay indicios coincidentes en registros y dominio.")
    analyzed = await analyze_case(
        services, investigator, narrator, job=claimed.job, attempt=claimed.attempt
    )
    assert isinstance(analyzed, Analyzed)
    assert analyzed.case.state is CaseState.VERDICT_ISSUED
    assert investigator.briefs[0].language == "es"
    assert narrator.briefs[0].level is RiskLevel.HIGH

    verdict = await services.ledger.current_verdict(case.id)
    assert verdict is not None
    assert (verdict.version, verdict.level, verdict.outcome) == (
        1,
        RiskLevel.HIGH,
        VerdictOutcome.ISSUED,
    )
    assert verdict.actions and verdict.missing == ()
    signals = await services.ledger.signals_of_case(case.id)
    assert {signal.analysis for signal in signals} == {Analysis.REGISTRIES, Analysis.DOMAIN}
    assert all(signal.quote and signal.source and signal.observed_at for signal in signals)
    entity = await services.ledger.entity_by_value(EntityKind.DOMAIN, domain)
    assert entity is not None
    assert [link.case_id for link in await services.ledger.cases_of_entity(entity.id)] == [case.id]

    closed = await services.ledger.job(job.id)
    assert closed is not None and closed.state is JobState.COMPLETED
    events = [
        entry
        for entry in await services.ledger.outbox_of_job(job.id)
        if entry.kind is OutboxKind.EVENT
    ]
    assert [(entry.subject, entry.state) for entry in events] == [
        (CASE_COMPLETED_SUBJECT, OutboxState.PENDING)
    ]

    again = await analyze_case(
        services, investigator, narrator, job=claimed.job, attempt=claimed.attempt
    )
    assert isinstance(again, Skipped)
    current = await services.ledger.current_verdict(case.id)
    assert current is not None and current.version == 1


async def test_failed_extraction_degrades_and_empty_case_is_insufficient(
    services: Services, tenant: Tenant
) -> None:
    """S02.36 una extracción fallida degrada el veredicto y sin entrada analizable el caso es insufficient."""
    degraded_case = await seed_case(services, tenant)
    await seed_extraction(services, tenant, degraded_case, texts=("fragmento legible",))
    failed = await failed_document_job(services, tenant, degraded_case)
    job = await seed_analysis_job(services, tenant, degraded_case)
    claimed = await claimed_analysis(services, job)
    investigator = ScriptedInvestigator(
        Investigation(
            signals=(signal_of(services.clock, Analysis.PATTERNS, Strength.WEAK),),
            entities=(),
            missing=(),
        )
    )
    analyzed = await analyze_case(
        services, investigator, ScriptedNarrator(), job=claimed.job, attempt=claimed.attempt
    )
    assert isinstance(analyzed, Analyzed)
    assert analyzed.case.state is CaseState.PARTIAL
    assert analyzed.verdict.outcome is VerdictOutcome.PARTIAL
    assert analyzed.verdict.level is not RiskLevel.LOW
    assert analyzed.verdict.missing == (f"document:{failed.document_id}",)

    empty_case = await seed_case(services, tenant)
    empty_job = await seed_analysis_job(services, tenant, empty_case)
    empty_claimed = await claimed_analysis(services, empty_job)
    nothing = await analyze_case(
        services,
        ScriptedInvestigator(Investigation(signals=(), entities=(), missing=())),
        ScriptedNarrator(),
        job=empty_claimed.job,
        attempt=empty_claimed.attempt,
    )
    assert isinstance(nothing, Analyzed)
    assert nothing.case.state is CaseState.INSUFFICIENT
    assert nothing.verdict.outcome is VerdictOutcome.INSUFFICIENT
    assert nothing.verdict.level is RiskLevel.UNDETERMINED
    assert nothing.verdict.actions


@pytest.fixture
async def surreal_services(
    anyio_backend: str, settings: Settings, ledger_schema: None, clock: FakeClock
) -> AsyncIterator[Services]:
    surreal = ledger_for(settings, "worker")
    await surreal.connect()
    try:
        yield build_services(surreal, FakeBus(), clock, InMemoryObjectStore())
    finally:
        await surreal.close()


async def agno_session_dump(settings: Settings) -> str:
    http = SurrealHttp(settings.surreal_url)
    info = await http.sql(
        "INFO FOR DB;",
        auth=settings.root_auth,
        ns=settings.agno_namespace,
        db=settings.agno_database,
    )
    database = info[-1].result
    assert isinstance(database, dict)
    dumped: list[str] = []
    for table in sorted(names_in(database.get("tables"))):
        rows = await http.sql(
            f"SELECT * FROM {table};",
            auth=settings.root_auth,
            ns=settings.agno_namespace,
            db=settings.agno_database,
        )
        dumped.append(json.dumps(rows[-1].result, default=str, ensure_ascii=False))
    return "\n".join(dumped)


async def test_real_cluster_analyses_without_leaking_text(
    settings: Settings, surreal_services: Services, tracing: TracerProvider
) -> None:
    """S02.37 el clúster real analiza con el modelo mock sin dejar texto en la sesión ni en la traza."""
    services = surreal_services
    marker = uuid4().hex
    fragment = f"fragmento sintetico {marker} del fixture"
    tenant = Tenant(id=f"t-{uuid4().hex[:12]}", name="tenant real", active=True, revision=0)
    await services.ledger.commit([Insert(tenant)])
    try:
        case = await seed_case(services, tenant)
        await seed_extraction(services, tenant, case, texts=(fragment,))
        job = await seed_analysis_job(services, tenant, case)
        claimed = await claimed_analysis(services, job)

        cluster = build_cluster(
            services,
            settings,
            tenant_id=tenant.id,
            case_id=case.id,
            db=build_agno_db(settings),
        )
        tracer = tracing.get_tracer("argos-tests")
        try:
            with tracer.start_as_current_span("s02-analysis") as root:
                analyzed = await analyze_case(
                    services,
                    cluster.investigator,
                    cluster.narrator,
                    job=claimed.job,
                    attempt=claimed.attempt,
                )
            trace_id = format(root.get_span_context().trace_id, "032x")
            tracing.force_flush()
        finally:
            await cluster.close()

        assert isinstance(analyzed, Analyzed)
        assert analyzed.case.state is CaseState.PARTIAL
        assert analyzed.verdict.outcome is VerdictOutcome.PARTIAL
        assert analyzed.verdict.level is RiskLevel.UNDETERMINED
        assert analyzed.verdict.actions
        assert set(analyzed.verdict.missing) == {str(Analysis(member)) for member in ANALYSES}
        assert not await services.ledger.signals_of_case(case.id)

        assert cluster.team.store_tool_messages is False
        assert all(agent.store_tool_messages is False for agent in cluster.specialists)
        assert marker not in investigation_prompt(await build_brief(services, case))

        sessions = await agno_session_dump(settings)
        assert f"case-{case.id}" in sessions
        assert marker not in sessions

        observations = await wait_for_observations(settings, trace_id=trace_id, timeout_seconds=60)
        assert observations
        assert all(
            marker not in json.dumps(observation, default=str, ensure_ascii=False)
            for observation in observations
        )
    finally:
        await services.ledger.delete_tenant_data(tenant.id)


CURATOR_TOKEN = "test-curator"
SERVICE_TOKEN = "test-service"
OTHER_TOKEN = "test-other"


class ScriptedAdvisor:
    def __init__(self, answer: str = "El nivel no cambia; revisa las acciones.") -> None:
        self._answer = answer
        self.briefs: list[ConversationBrief] = []

    async def answer(self, brief: ConversationBrief) -> str:
        self.briefs.append(brief)
        return self._answer


def identities_of(tenant: Tenant, stranger: Tenant) -> dict[str, Identity]:
    return {
        SERVICE_TOKEN: Identity(name="dev", role=Role.SERVICE, tenant_id=tenant.id),
        OTHER_TOKEN: Identity(name="ajeno", role=Role.SERVICE, tenant_id=stranger.id),
        CURATOR_TOKEN: Identity(name="curador", role=Role.CURATOR, tenant_id=None),
    }


def build_gateway_app(
    services: Services,
    identities: dict[str, Identity],
    advisor: ScriptedAdvisor,
    clock: FakeClock,
) -> FastAPI:
    async def sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    gateway = Gateway(
        services=services,
        advisors=lambda tenant_id, case_id: advisor,
        identities=identities,
        version="test",
        public_url="http://argos.test",
        sleep=sleep,
    )
    return build_app(gateway)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def stranger(ledger: Ledger) -> AsyncIterator[Tenant]:
    record = Tenant(id=f"t-{uuid4().hex[:12]}", name="tenant ajeno", active=True, revision=0)
    await ledger.commit([Insert(record)])
    try:
        yield record
    finally:
        await ledger.delete_tenant_data(record.id)


@pytest.fixture
def advisor() -> ScriptedAdvisor:
    return ScriptedAdvisor()


@pytest.fixture
def gateway_app(
    services: Services,
    tenant: Tenant,
    stranger: Tenant,
    advisor: ScriptedAdvisor,
    clock: FakeClock,
) -> FastAPI:
    return build_gateway_app(services, identities_of(tenant, stranger), advisor, clock)


def asgi_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://argos.test")


@pytest.fixture
async def client(anyio_backend: str, gateway_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with asgi_client(gateway_app) as running:
        yield running


async def test_gateway_derives_the_tenant_from_the_identity(
    client: httpx.AsyncClient, tenant: Tenant, stranger: Tenant
) -> None:
    """S02.38 el gateway deriva el tenant de la identidad y nunca del cuerpo."""
    body = {"text": "Invierte con Nexolabs y dobla tu dinero", "tenant_id": stranger.id}
    assert (await client.post("/v1/notices", json=body)).status_code == 401
    unknown = await client.post("/v1/notices", json=body, headers=bearer("desconocido"))
    assert unknown.status_code == 401

    accepted = await client.post("/v1/notices", json=body, headers=bearer(SERVICE_TOKEN))
    assert accepted.status_code in (200, 202)
    case_id = str(accepted.json()["case_id"])
    mine = await client.get(f"/v1/cases/{case_id}", headers=bearer(SERVICE_TOKEN))
    theirs = await client.get(f"/v1/cases/{case_id}", headers=bearer(OTHER_TOKEN))
    assert mine.status_code == 200 and theirs.status_code == 404

    curator = await client.post("/v1/notices", json=body, headers=bearer(CURATOR_TOKEN))
    assert curator.status_code == 403
    assert curator.json() == {"error": "identity.not_a_tenant"}
    denied = await client.post("/v1/documents/x/reprocess", headers=bearer(SERVICE_TOKEN))
    assert denied.status_code == 403 and denied.json() == {"error": "identity.not_curator"}


async def test_agentos_publishes_capabilities_only(
    gateway_app: FastAPI, settings: Settings
) -> None:
    """S02.39 AgentOS publica capacidades y no descubre especialistas ni workers."""
    served = AgentOS(
        id="argos-test",
        name="Argos",
        base_app=gateway_app,
        db=build_agno_db(settings),
        telemetry=False,
    )
    app = served.get_app()
    business = {
        (route.path, method)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/v1")
        for method in route.methods or set()
    }
    assert business == {(spec.path, spec.method) for spec in GATEWAY_CAPABILITIES} | {
        (MESSAGES_PATH, "POST")
    }

    async with asgi_client(app) as client:
        card = await client.get(CARD_PATH)
        listed = await client.get("/agents", headers=bearer(CURATOR_TOKEN))
        teams = await client.get("/teams", headers=bearer(CURATOR_TOKEN))
        anonymous = await client.get("/agents")
        service = await client.get("/agents", headers=bearer(SERVICE_TOKEN))
    rendered = card.text
    skills = card.json()["skills"]
    assert {str(skill["id"]) for skill in skills} == {
        str(spec.name) for spec in GATEWAY_CAPABILITIES
    }
    assert all(str(agent) not in rendered for agent in AgentName)
    assert TEAM_NAME not in rendered and "worker" not in rendered
    assert listed.json() == [] and teams.json() == []
    assert anonymous.status_code == 401 and service.status_code == 403


async def analyzed_by(
    services: Services, job_id: str, *, signals: tuple[DraftSignal, ...] = ()
) -> Analyzed:
    job = await services.ledger.job(job_id)
    assert job is not None
    claimed = await claimed_analysis(services, job)
    analyzed = await analyze_case(
        services,
        ScriptedInvestigator(Investigation(signals=signals, entities=(), missing=())),
        ScriptedNarrator(),
        job=claimed.job,
        attempt=claimed.attempt,
    )
    assert isinstance(analyzed, Analyzed)
    return analyzed


async def test_analyze_notice_returns_within_the_budget(
    client: httpx.AsyncClient, services: Services, clock: FakeClock
) -> None:
    """S02.40 analyze_notice devuelve el veredicto dentro del presupuesto y el caso sobrevive al proceso."""
    waiting = await client.post(
        "/v1/notices",
        json={"text": "Rentabilidad garantizada del 40% en Nexolabs Capital"},
        headers=bearer(SERVICE_TOKEN),
    )
    assert waiting.status_code == 202
    accepted = waiting.json()
    assert accepted["state"] == str(CaseState.RECEIVED) and accepted["verdict"] is None
    job = await services.ledger.job(str(accepted["job_id"]))
    assert job is not None and job.type is JobType.CASE_ANALYZE

    await analyzed_by(services, str(accepted["job_id"]))
    recovered = await client.get(f"/v1/cases/{accepted['case_id']}", headers=bearer(SERVICE_TOKEN))
    assert recovered.status_code == 200
    assert recovered.json()["state"] == str(CaseState.VERDICT_ISSUED)
    assert recovered.json()["verdict"]["version"] == 1

    settled = await client.post(
        "/v1/notices",
        json={"text": "Rentabilidad garantizada del 40% en Nexolabs Capital"},
        headers=bearer(SERVICE_TOKEN),
    )
    assert settled.status_code == 200
    assert settled.json()["reused"] and settled.json()["verdict"]["version"] == 1


async def test_documents_are_accepted_before_extraction(
    client: httpx.AsyncClient, store: InMemoryObjectStore
) -> None:
    """S02.41 enviar un documento por la API responde antes de extraer."""
    accepted = await client.post(
        "/v1/documents",
        files={"file": ("aviso.pdf", PDF_BYTES, "application/pdf")},
        headers=bearer(SERVICE_TOKEN),
    )
    assert accepted.status_code == 202
    body = accepted.json()
    assert body["job_state"] == str(JobState.QUEUED) and not body["reused"]
    assert store.objects

    seen = await client.get(f"/v1/jobs/{body['job_id']}", headers=bearer(SERVICE_TOKEN))
    assert seen.status_code == 200
    assert seen.json()["state"] == str(JobState.QUEUED)
    assert seen.json()["public_error"] is None

    rejected = await client.post(
        "/v1/documents",
        files={"file": ("aviso.pdf", b"no soy un pdf", "application/pdf")},
        headers=bearer(SERVICE_TOKEN),
    )
    assert rejected.status_code == 422
    assert rejected.json() == {"error": "document.not_pdf"}


async def test_api_hides_other_tenants(client: httpx.AsyncClient) -> None:
    """S02.42 la API no deja ver el caso de otro tenant."""
    accepted = await client.post(
        "/v1/documents",
        files={"file": ("aviso.pdf", PDF_BYTES, "application/pdf")},
        headers=bearer(SERVICE_TOKEN),
    )
    body = accepted.json()
    job = await client.get(f"/v1/jobs/{body['job_id']}", headers=bearer(OTHER_TOKEN))
    case = await client.get(f"/v1/cases/{body['case_id']}", headers=bearer(OTHER_TOKEN))
    question = await client.post(
        f"/v1/cases/{body['case_id']}/questions",
        json={"question": "¿qué sabes?"},
        headers=bearer(OTHER_TOKEN),
    )
    assert [job.status_code, case.status_code, question.status_code] == [404, 404, 404]
    assert job.json() == {"error": "job.not_found"}
    assert case.json() == {"error": "case.not_found"}


async def test_ask_case_answers_without_touching_the_verdict(
    client: httpx.AsyncClient,
    services: Services,
    tenant: Tenant,
    advisor: ScriptedAdvisor,
    clock: FakeClock,
) -> None:
    """S02.43 ask_case responde con la evidencia persistida y no muta el veredicto."""
    case = await seed_case(services, tenant)
    await seed_extraction(services, tenant, case, texts=("promesa de rentabilidad",))
    job = await seed_analysis_job(services, tenant, case)
    await analyzed_by(services, job.id, signals=(signal_of(clock, Analysis.REGISTRIES),))

    answered = await client.post(
        f"/v1/cases/{case.id}/questions",
        json={"question": "¿Puedo recuperar el dinero?"},
        headers=bearer(SERVICE_TOKEN),
    )
    assert answered.status_code == 200
    assert answered.json()["answer"] == "El nivel no cambia; revisa las acciones."
    assert advisor.briefs[0].quotes and advisor.briefs[0].level is RiskLevel.MEDIUM

    verdict = await services.ledger.current_verdict(case.id)
    assert verdict is not None and (verdict.version, verdict.level) == (1, RiskLevel.MEDIUM)
    assert len(await services.ledger.signals_of_case(case.id)) == 1

    pending = await seed_case(services, tenant)
    unanswered = await client.post(
        f"/v1/cases/{pending.id}/questions",
        json={"question": "¿ya está?"},
        headers=bearer(SERVICE_TOKEN),
    )
    assert unanswered.status_code == 200
    assert unanswered.json()["answer"] == NO_VERDICT_YET
    assert unanswered.json()["verdict"] is None


async def test_only_the_curator_reprocesses(
    client: httpx.AsyncClient, services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.44 reprocesar es del curador, conserva la extracción y supera el veredicto."""
    submitted = await accepted_submission(services, tenant)
    await complete_document_job(services, submitted.job_id)
    queued = await resume_case(services, JobMessage(job_id=submitted.job_id, attempt=1))
    assert isinstance(queued, AnalysisQueued)
    first = await analyzed_by(services, queued.job_id, signals=(signal_of(clock, Analysis.DOMAIN),))
    assert first.case.state is CaseState.VERDICT_ISSUED

    refused = await client.post(
        f"/v1/documents/{submitted.document_id}/reprocess", headers=bearer(SERVICE_TOKEN)
    )
    assert refused.status_code == 403
    assert len(await services.ledger.jobs_of_case(submitted.case_id)) == 2

    again = await client.post(
        f"/v1/documents/{submitted.document_id}/reprocess", headers=bearer(CURATOR_TOKEN)
    )
    assert again.status_code == 202
    reprocessing = again.json()
    assert reprocessing["options"] == reprocess_options(2)
    fresh = await services.ledger.job(str(reprocessing["job_id"]))
    assert fresh is not None and fresh.previous_job_id == submitted.job_id
    case = await services.ledger.case(submitted.case_id)
    assert case is not None and case.state is CaseState.AWAITING_PROCESSING
    kept = await services.ledger.extractions_of_document(submitted.document_id)
    assert [extraction.state for extraction in kept] == [ExtractionState.AVAILABLE]

    await complete_document_job(services, fresh.id)
    requeued = await resume_case(services, JobMessage(job_id=fresh.id, attempt=1))
    assert isinstance(requeued, AnalysisQueued)
    second = await analyzed_by(services, requeued.job_id)
    assert second.verdict.version == 2
    current = await services.ledger.current_verdict(submitted.case_id)
    assert current is not None and current.version == 2


async def test_lost_analysis_attempt_does_not_duplicate_the_verdict(
    services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.45 un analizador que muere con el intento abierto no duplica el veredicto."""
    case = await seed_case(services, tenant)
    job = await seed_analysis_job(services, tenant, case)
    abandoned = await claimed_analysis(services, job)
    assert abandoned.job.state is JobState.RUNNING

    clock.advance(TEST_POLICY.jobs.lease + timedelta(seconds=1))
    recovery = await recover_leases_once(services.dispatching)
    assert recovery.requeued == (job.id,)
    lost = await services.ledger.attempts(job.id)
    assert [attempt.state for attempt in lost] == [AttemptState.LOST]

    clock.advance(TEST_POLICY.jobs.backoff(1))
    retried = await claim_attempt(
        services, JobMessage(job_id=job.id, attempt=2), consumer=CASE_ANALYZER.durable
    )
    assert isinstance(retried, ClaimedAttempt)
    analyzed = await analyze_case(
        services,
        ScriptedInvestigator(Investigation(signals=(), entities=(), missing=())),
        ScriptedNarrator(),
        job=retried.job,
        attempt=retried.attempt,
    )
    assert isinstance(analyzed, Analyzed)
    assert analyzed.case.state in TERMINAL_CASE_STATES

    verdicts = [analyzed.verdict.version]
    assert verdicts == [1]
    events = [
        entry
        for entry in await services.ledger.outbox_of_job(job.id)
        if entry.kind is OutboxKind.EVENT
    ]
    assert [entry.subject for entry in events] == [CASE_COMPLETED_SUBJECT]


async def test_document_on_a_settled_case_creates_a_linked_case(
    services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.46 un documento enviado a un caso con veredicto crea un caso vinculado."""
    case = await seed_case(services, tenant)
    await seed_extraction(services, tenant, case, texts=("promesa de rentabilidad",))
    job = await seed_analysis_job(services, tenant, case)
    settled = await analyzed_by(services, job.id, signals=(signal_of(clock, Analysis.PATTERNS),))
    assert settled.case.state is CaseState.VERDICT_ISSUED

    submitted = await submit_document(services, upload_of(tenant, case_id=case.id))
    assert isinstance(submitted, DocumentAccepted)
    assert submitted.case_id != case.id
    linked = await services.ledger.case(submitted.case_id)
    assert linked is not None and linked.previous_case_id == case.id
    assert linked.state is CaseState.AWAITING_PROCESSING

    document = await services.ledger.document(submitted.document_id)
    assert document is not None and document.case_id == linked.id
    unchanged = await services.ledger.current_verdict(case.id)
    assert unchanged is not None and unchanged.version == settled.verdict.version
    frozen = await services.ledger.case(case.id)
    assert frozen is not None and frozen.state is CaseState.VERDICT_ISSUED


async def test_each_workload_has_its_own_identity(settings: Settings, ledger_schema: None) -> None:
    """S02.47 cada workload tiene su identidad y la de los agentes es de solo lectura."""
    await apply_schema(settings)
    await apply_schema(settings)
    http = SurrealHttp(settings.surreal_url)
    info = await http.sql(
        "INFO FOR DB;", auth=settings.root_auth, ns=settings.ops_namespace, db=settings.ops_database
    )
    database = info[-1].result
    assert isinstance(database, dict)
    users = names_in(database.get("users"))
    assert set(WORKLOADS) <= users
    assert "ledger" not in users

    for name, neighbour in zip(WORKLOADS, WORKLOADS[1:] + WORKLOADS[:1], strict=True):
        credentials = settings.workload(name)
        token = await http.sign_in(
            ns=settings.ops_namespace,
            db=settings.ops_database,
            user=credentials.user,
            password=credentials.password.get_secret_value(),
        )
        assert token
        with pytest.raises(SurrealError):
            await http.sign_in(
                ns=settings.ops_namespace,
                db=settings.ops_database,
                user=credentials.user,
                password=settings.workload(neighbour).password.get_secret_value(),
            )

    agent_auth = await http.sign_in(
        ns=settings.ops_namespace,
        db=settings.ops_database,
        user=settings.surreal_agent_user,
        password=settings.surreal_agent_password.get_secret_value(),
    )
    readable = await http.sql(
        "SELECT version FROM schema_version:current;",
        auth=agent_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
    )
    assert readable[-1].result

    intruder = f"intruso{uuid4().hex[:8]}"
    await http.sql(
        f"CREATE tenant:{intruder} SET name = 'x', active = true, revision = 0;",
        auth=agent_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
        raise_on_error=False,
    )
    written = await http.sql(
        f"SELECT * FROM tenant:{intruder};",
        auth=settings.root_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
    )
    assert written[-1].result == []
    escalation = await http.sql(
        "DEFINE USER hacker ON DATABASE PASSWORD 'x' ROLES OWNER;",
        auth=agent_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
        raise_on_error=False,
    )
    assert not escalation[-1].ok
    assert "Not enough permissions" in str(escalation[-1].result)


async def staged_artifact(
    services: Services, tenant: Tenant, case: Case, *, expires_in: timedelta
) -> Artifact:
    now = services.clock.now()
    artifact = Artifact(
        id=services.ids.new_id(),
        tenant_id=tenant.id,
        case_id=case.id,
        bucket=services.bucket,
        key=probe_key(f"{uuid4().hex[:8]}.pdf"),
        state=ArtifactState.UPLOADING,
        sha256=None,
        size=0,
        mime="application/pdf",
        created_at=now,
        expires_at=now + expires_in,
        revision=0,
    )
    await services.ledger.commit([Insert(artifact)])
    await services.object_store.put(
        artifact.key, chunks_of(PDF_BYTES), size=len(PDF_BYTES), mime="application/pdf"
    )
    return artifact


class UnavailableStore:
    """Un almacén que no puede borrar: el barrido no debe dar por hecho el borrado."""

    def __init__(self, inner: InMemoryObjectStore) -> None:
        self._inner = inner

    async def put(
        self, key: str, content: AsyncIterable[bytes], *, size: int, mime: str
    ) -> StoredObject:
        return await self._inner.put(key, content, size=size, mime=mime)

    async def read(self, key: str, *, limit: int) -> bytes | None:
        return await self._inner.read(key, limit=limit)

    async def stat(self, key: str) -> ObjectMetadata | None:
        return await self._inner.stat(key)

    async def delete(self, key: str) -> None:
        raise ObjectStoreError(f"el almacén no responde para {key}")

    def presigned_get(self, key: str, *, expires_in: timedelta) -> str:
        return self._inner.presigned_get(key, expires_in=expires_in)


async def test_janitor_sweeps_interrupted_uploads(
    services: Services, tenant: Tenant, clock: FakeClock, store: InMemoryObjectStore
) -> None:
    """S02.48 el janitor borra la subida interrumpida y su objeto al vencer el TTL."""
    case = await seed_case(services, tenant)
    stale = await staged_artifact(services, tenant, case, expires_in=timedelta(hours=1))
    fresh = await staged_artifact(
        services, tenant, case, expires_in=TEST_POLICY.retention.staging * 4
    )
    submitted = await accepted_submission(services, tenant)
    document = await services.ledger.document(submitted.document_id)
    assert document is not None
    abandoned = await claimed(services, submitted.job_id, 1, "worker-a")
    reserved = await reserved_derivatives(services, abandoned.job, services.ids.new_id())

    clock.advance(TEST_POLICY.retention.staging + timedelta(hours=1))
    unavailable = replace(services, object_store=UnavailableStore(store))
    blocked = await sweep_staging(unavailable)
    assert blocked.swept == () and blocked.removed == ()
    assert set(blocked.skipped) == {stale.id, *reserved}
    still_there = await services.ledger.artifact(stale.id)
    assert still_there is not None and still_there.state is ArtifactState.UPLOADING
    assert await store.stat(stale.key) is not None

    report = await sweep_staging(services)
    assert set(report.swept) == {stale.id, *reserved}
    assert stale.key in report.removed

    swept = await services.ledger.artifact(stale.id)
    assert swept is not None and swept.state is ArtifactState.DELETED
    assert await store.stat(stale.key) is None
    untouched = await services.ledger.artifact(fresh.id)
    assert untouched is not None and untouched.state is ArtifactState.UPLOADING
    assert await store.stat(fresh.key) is not None
    live = await services.ledger.artifact(document.artifact_id)
    assert live is not None and live.state is ArtifactState.AVAILABLE
    assert await store.stat(live.key) is not None
    for artifact_id in reserved:
        recovered = await services.ledger.artifact(artifact_id)
        assert recovered is not None and recovered.state is ArtifactState.DELETED
    assert (await services.ledger.job(submitted.job_id)) is not None


async def test_retention_removes_expired_content_only(
    services: Services, tenant: Tenant, clock: FakeClock, store: InMemoryObjectStore
) -> None:
    """S02.49 la retención borra el contenido caducado sin dañar el caso."""
    case = await seed_case(services, tenant)
    await seed_extraction(services, tenant, case, texts=("fragmento caducado",))
    job = await seed_analysis_job(services, tenant, case)
    await analyzed_by(services, job.id, signals=(signal_of(clock, Analysis.PATTERNS),))
    expired = (await services.ledger.documents_of_case(case.id))[0]
    extraction = (await services.ledger.extractions_of_document(expired.id))[0]
    for artifact_id in (
        expired.artifact_id,
        extraction.text_artifact_id,
        extraction.manifest_artifact_id,
    ):
        await services.ledger.commit(
            [
                Insert(
                    Artifact(
                        id=artifact_id,
                        tenant_id=tenant.id,
                        case_id=case.id,
                        bucket=services.bucket,
                        key=probe_key(f"{artifact_id}.bin"),
                        state=ArtifactState.AVAILABLE,
                        sha256=hashlib.sha256(artifact_id.encode()).hexdigest(),
                        size=len(PDF_BYTES),
                        mime="application/pdf",
                        created_at=clock.now(),
                        expires_at=clock.now() + timedelta(days=30),
                        revision=0,
                    )
                )
            ]
        )
        stored = await services.ledger.artifact(artifact_id)
        assert stored is not None
        await services.object_store.put(
            stored.key, chunks_of(PDF_BYTES), size=len(PDF_BYTES), mime="application/pdf"
        )

    clock.advance(TEST_POLICY.retention.full_content + timedelta(days=1))
    survivor = await seed_case(services, tenant)
    kept = await seed_extraction(services, tenant, survivor, texts=("fragmento vigente",))
    report = await enforce_retention(services)
    assert expired.id in report.swept
    mine = {key for key in report.removed if key.startswith("tenants/probe-")}
    assert len(mine) == 3

    assert await services.ledger.chunks(extraction.id) == []
    aged = await services.ledger.extraction(extraction.id)
    assert aged is not None and aged.state is ExtractionState.EXPIRED
    gone = await services.ledger.document(expired.id)
    assert gone is not None and gone.state is DocumentState.EXPIRED
    for key in mine:
        assert await store.stat(key) is None

    assert await services.ledger.case(case.id) is not None
    assert len(await services.ledger.signals_of_case(case.id)) == 1
    assert await services.ledger.current_verdict(case.id) is not None
    assert len(await services.ledger.chunks(kept.extraction_id)) == 1

    again = await enforce_retention(services)
    assert expired.id not in again.swept


async def test_store_survives_the_rehearsal(rustfs_store: RustFsObjectStore) -> None:
    """S02.50 el almacén supera el ensayo de escritura, verificación, borrado y restauración."""
    report = await rehearse(rustfs_store, key=probe_key(f"{uuid4().hex}.txt"))
    assert report.written and report.verified and report.read_back
    assert report.deleted and report.restored
    assert report.passed
    assert await rustfs_store.stat(report.key) is None


async def test_public_errors_do_not_leak_internals(services: Services, tenant: Tenant) -> None:
    """S02.51 el error público no filtra claves, SQL ni texto del documento."""
    submitted = await accepted_submission(services, tenant)
    claimed = await claim_attempt(
        services,
        JobMessage(job_id=submitted.job_id, attempt=1),
        consumer=DOCUMENT_EXTRACTOR.durable,
    )
    assert isinstance(claimed, ClaimedAttempt)
    leaked = (
        "SELECT * FROM document WHERE key = "
        "'tenants/t1/cases/c1/documents/d1/source.pdf' -- promesa de rentabilidad"
    )
    await fail_attempt(
        services,
        job_id=submitted.job_id,
        attempt_number=1,
        kind=FailureKind.PERMANENT,
        code=leaked,
    )

    stored = await services.ledger.job(submitted.job_id)
    assert stored is not None and stored.internal_error == leaked
    view = await get_job(services, tenant_id=tenant.id, job_id=submitted.job_id)
    assert view is not None
    assert public_code(view.public_error) == INTERNAL_ERROR
    rendered = dumps(job_payload(view))
    assert "SELECT" not in rendered and "source.pdf" not in rendered
    assert "rentabilidad" not in rendered
    assert public_code(PdfEncryptedError.code) == PdfEncryptedError.code


async def test_traces_correlate_without_sensitive_content(
    services: Services, tenant: Tenant, tracing: TracerProvider, clock: FakeClock
) -> None:
    """S02.52 la traza correlaciona la cadena sin contenido sensible."""
    collected = InMemorySpanExporter()
    tracing.add_span_processor(SimpleSpanProcessor(collected))
    marker = uuid4().hex
    try:
        submitted = await accepted_submission(services, tenant)
        claimed = await claim_attempt(
            services,
            JobMessage(job_id=submitted.job_id, attempt=1),
            consumer=DOCUMENT_EXTRACTOR.durable,
        )
        assert isinstance(claimed, ClaimedAttempt)
        tools = PdfTools(reader=PdfiumReader(), ocr=RecordingOcr(text=f"texto {marker}"))
        extracted = await extract_document(
            services, tools, job=claimed.job, attempt=claimed.attempt
        )
        assert not isinstance(extracted, Skipped)

        case = await services.ledger.case(submitted.case_id)
        assert case is not None
        analysis = await seed_analysis_job(services, tenant, case)
        analyzing = await claimed_analysis(services, analysis)
        await analyze_case(
            services,
            ScriptedInvestigator(Investigation(signals=(), entities=(), missing=())),
            ScriptedNarrator(),
            job=analyzing.job,
            attempt=analyzing.attempt,
        )
        tracing.force_flush()
        spans = [
            span
            for span in collected.get_finished_spans()
            if span.name in ("argos.extract", "argos.analyze")
        ]
    finally:
        collected.shutdown()

    assert {span.name for span in spans} == {"argos.extract", "argos.analyze"}
    attributes = [dict(span.attributes or {}) for span in spans]
    assert {str(found["argos.correlation_id"]) for found in attributes} == {case.correlation_id}
    assert all(found["argos.tenant_id"] == tenant.id for found in attributes)
    assert all(found["argos.case_id"] == submitted.case_id for found in attributes)
    extraction = next(found for found in attributes if found["argos.pages"])
    assert extraction["argos.bytes"] == len(PDF_BYTES)
    assert extraction["argos.document_id"] == submitted.document_id

    rendered = json.dumps(attributes, default=str, ensure_ascii=False)
    assert marker not in rendered
    assert "source.pdf" not in rendered and "tenants/" not in rendered
    assert "SELECT" not in rendered


async def test_metrics_are_curator_only(
    client: httpx.AsyncClient, services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.53 las métricas mínimas salen del libro y solo las ve el curador."""
    submitted = await accepted_submission(services, tenant)
    await seed_analysis_job(services, tenant, await seed_case(services, tenant))
    clock.advance(timedelta(minutes=5))

    denied = await client.get("/metrics", headers=bearer(SERVICE_TOKEN))
    anonymous = await client.get("/metrics")
    assert denied.status_code == 403 and anonymous.status_code == 401

    seen = await client.get("/metrics", headers=bearer(CURATOR_TOKEN))
    assert seen.status_code == 200
    metrics = seen.json()
    assert metrics["jobs"][f"{JobType.DOCUMENT_EXTRACT}.{JobState.QUEUED}"] >= 1
    assert metrics["jobs"][f"{JobType.CASE_ANALYZE}.{JobState.QUEUED}"] >= 1
    assert metrics["oldest_queued_seconds"] >= 300
    assert metrics["pending_outbox"] >= 1
    assert metrics["awaiting_documents"] >= 1
    assert metrics["failed"] == 0

    rendered = seen.text
    assert submitted.case_id not in rendered and submitted.job_id not in rendered
    assert tenant.id not in rendered


async def test_demo_warnings_are_queryable(
    settings: Settings,
) -> None:
    """S02.54 el catálogo sintético demuestra la consulta del agente de registros."""
    ledger = InMemoryLedger()
    bundle = parse_knowledge_bundle(read_bundle(settings.knowledge_graph_path))
    warnings = warnings_from_bundle(bundle)
    await ledger.commit([Insert(warning) for warning in warnings])

    matches = await find_registry_matches(
        Services(
            ledger=ledger,
            object_store=InMemoryObjectStore(),
            bus=FakeBus(),
            clock=FakeClock(),
            ids=SequentialIds(),
            policy=TEST_POLICY,
            bucket="argos",
        ),
        ToolCaller(
            agent=AgentName.REGISTRIES,
            tenant_id="tenant-demo",
            case_id="case-demo",
        ),
        kind=EntityKind.DOMAIN,
        value="example-broker.test",
    )
    assert not isinstance(matches, ToolDenied)
    assert [(match.regulator, match.url, match.captured_at, match.active) for match in matches] == [
        (
            "FCA",
            "https://warnings.fca.example/demo/example-broker",
            "2026-09-01T00:00:00+00:00",
            True,
        )
    ]
    withdrawn = await find_registry_matches(
        Services(
            ledger=ledger,
            object_store=InMemoryObjectStore(),
            bus=FakeBus(),
            clock=FakeClock(),
            ids=SequentialIds(),
            policy=TEST_POLICY,
            bucket="argos",
        ),
        ToolCaller(
            agent=AgentName.REGISTRIES,
            tenant_id="tenant-demo",
            case_id="case-demo",
        ),
        kind=EntityKind.DOMAIN,
        value="retired-platform.test",
    )
    assert not isinstance(withdrawn, ToolDenied)
    assert [match.active for match in withdrawn] == [False]


def test_services_profile_bootstraps_and_runs_argos(settings: Settings) -> None:
    """S02.55 el perfil services prepara y ejecuta Argos sin pasos manuales."""
    compose = Path(".devcontainer/docker-compose.yml").read_text(encoding="utf-8")

    def service_block(name: str) -> str:
        match = re.search(
            rf"^  {re.escape(name)}:\n(?P<body>(?:(?:    .*)?\n)*)",
            compose,
            re.MULTILINE,
        )
        assert match is not None, f"falta el servicio {name}"
        return match.group(0)

    bootstrap = service_block("bootstrap")
    assert 'profiles: ["services"]' in bootstrap
    assert 'command: ["uv", "run", "--frozen", "bootstrap-local"]' in bootstrap

    for service, command in {
        "gateway": "gateway",
        "dispatcher": "dispatcher",
        "worker": "worker",
        "resumer": "resumer",
        "analyzer": "analyzer",
        "janitor": "janitor",
    }.items():
        block = service_block(service)
        assert 'profiles: ["services"]' in block
        assert f'command: ["uv", "run", "--frozen", "{command}"]' in block
        assert "bootstrap:" in block
        assert "condition: service_completed_successfully" in block

    gateway = service_block("gateway")
    assert '"127.0.0.1:${AGENTOS_PORT:-7777}:7777"' in gateway
    assert '"127.0.0.1:${AGENTOS_PORT:-7777}:7777"' not in service_block("app")

    devcontainer = json.loads(
        Path(".devcontainer/devcontainer.json").read_text(encoding="utf-8")
    )
    assert set(devcontainer["runServices"]) == {
        "app",
        "gateway",
        "dispatcher",
        "worker",
        "resumer",
        "analyzer",
        "janitor",
        "surrealdb-test",
        "nats-test",
    }
    assert "postCreateCommand" not in devcontainer
    assert "postStartCommand" not in devcontainer
    assert settings.surreal_url == "http://surrealdb-test:8000"
    assert settings.nats_url == "nats://nats-test:4222"
    assert service_block("surrealdb-test") and service_block("nats-test")
    assert 'bootstrap-local = "argos.devtools.bootstrap_local:main"' in Path(
        "pyproject.toml"
    ).read_text(encoding="utf-8")
