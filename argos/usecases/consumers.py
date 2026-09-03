"""Lado consumidor de un comando: reclamar el intento, cerrarlo con fallo o con extracción."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.keys import extraction_manifest_key, extraction_text_key
from argos.core.ledger import (
    ChunkDraft,
    ExtractionDraft,
    Obsolete,
    plan_attempt_claim,
    plan_attempt_failure,
    plan_extraction_completion,
    plan_lease_renewal,
)
from argos.core.messages import JobMessage
from argos.core.model import (
    Artifact,
    ArtifactState,
    Attempt,
    FailureKind,
    Job,
    JobState,
)
from argos.core.ports import LedgerConflictError, StoredObject
from argos.usecases.deps import Services


@dataclass(frozen=True)
class ClaimedAttempt:
    job: Job
    attempt: Attempt


@dataclass(frozen=True)
class Skipped:
    """La entrega se confirma sin efecto: el intento no es el vigente o ya se cerró."""

    reason: str


async def claim_attempt(
    services: Services, message: JobMessage, *, consumer: str
) -> ClaimedAttempt | Skipped:
    job = await services.ledger.job(message.job_id)
    if job is None:
        return Skipped("unknown job")
    plan = plan_attempt_claim(
        job=job,
        attempt_number=message.attempt,
        consumer=consumer,
        now=services.clock.now(),
        policy=services.policy.jobs,
    )
    if isinstance(plan, Obsolete):
        return Skipped(plan.reason)
    try:
        await services.ledger.commit(plan.ops)
    except LedgerConflictError:
        return Skipped("claimed by another consumer")
    return ClaimedAttempt(job=plan.job, attempt=plan.attempt)


async def renew_lease(services: Services, job_id: str) -> Job | Skipped:
    job = await services.ledger.job(job_id)
    if job is None or job.state is not JobState.RUNNING:
        return Skipped("job is not running")
    ops = plan_lease_renewal(job=job, now=services.clock.now(), policy=services.policy.jobs)
    try:
        await services.ledger.commit(ops)
    except LedgerConflictError:
        return Skipped("job changed underneath")
    renewed = await services.ledger.job(job_id)
    return renewed if renewed is not None else Skipped("job vanished")


async def _current_attempt(services: Services, job: Job, number: int) -> Attempt | None:
    attempts = [
        attempt for attempt in await services.ledger.attempts(job.id) if attempt.number == number
    ]
    return attempts[0] if attempts else None


async def fail_attempt(
    services: Services,
    *,
    job_id: str,
    attempt_number: int,
    kind: FailureKind,
    code: str,
) -> Job | Skipped:
    ledger = services.ledger
    job = await ledger.job(job_id)
    if job is None:
        return Skipped("unknown job")
    attempt = await _current_attempt(services, job, attempt_number)
    if attempt is None:
        return Skipped("unknown attempt")
    document = await ledger.document(job.document_id) if job.document_id else None
    plan = plan_attempt_failure(
        job=job,
        attempt=attempt,
        now=services.clock.now(),
        policy=services.policy.jobs,
        kind=kind,
        code=code,
        document=document,
    )
    if isinstance(plan, Obsolete):
        return Skipped(plan.reason)
    try:
        await ledger.commit(plan.ops)
    except LedgerConflictError:
        return Skipped("job changed underneath")
    return plan.job


@dataclass(frozen=True)
class ExtractedChunk:
    page: int
    position: int
    text: str
    sha256: str


@dataclass(frozen=True)
class ExtractionResult:
    text_object: StoredObject
    manifest_object: StoredObject
    sha256: str
    page_count: int
    ocr_pages: int
    chunks: tuple[ExtractedChunk, ...]


async def complete_extraction(
    services: Services, *, job_id: str, attempt_number: int, result: ExtractionResult
) -> Job | Skipped:
    ledger = services.ledger
    job = await ledger.job(job_id)
    if job is None or job.document_id is None:
        return Skipped("unknown job")
    attempt = await _current_attempt(services, job, attempt_number)
    if attempt is None:
        return Skipped("unknown attempt")
    if job.state is not JobState.RUNNING or job.attempt != attempt_number:
        return Skipped("attempt is not the running one")
    document = await ledger.document(job.document_id)
    if document is None:
        return Skipped("unknown document")
    now = services.clock.now()
    expires_at = now + services.policy.retention.full_content
    extraction_id = services.ids.new_id()
    text_artifact = Artifact(
        id=services.ids.new_id(),
        tenant_id=job.tenant_id,
        case_id=job.case_id,
        bucket=services.bucket,
        key=extraction_text_key(job.tenant_id, job.case_id, extraction_id),
        state=ArtifactState.AVAILABLE,
        sha256=result.text_object.sha256,
        size=result.text_object.size,
        mime="application/zstd",
        created_at=now,
        expires_at=expires_at,
        revision=0,
    )
    manifest_artifact = Artifact(
        id=services.ids.new_id(),
        tenant_id=job.tenant_id,
        case_id=job.case_id,
        bucket=services.bucket,
        key=extraction_manifest_key(job.tenant_id, job.case_id, extraction_id),
        state=ArtifactState.AVAILABLE,
        sha256=result.manifest_object.sha256,
        size=result.manifest_object.size,
        mime="application/json",
        created_at=now,
        expires_at=expires_at,
        revision=0,
    )
    draft = ExtractionDraft(
        extraction_id=extraction_id,
        text_artifact=text_artifact,
        manifest_artifact=manifest_artifact,
        sha256=result.sha256,
        page_count=result.page_count,
        ocr_pages=result.ocr_pages,
        chunks=tuple(
            ChunkDraft(page=c.page, position=c.position, text=c.text, sha256=c.sha256)
            for c in result.chunks
        ),
        chunk_ids=tuple(services.ids.new_id() for _ in result.chunks),
    )
    plan = plan_extraction_completion(
        job=job,
        attempt=attempt,
        document=document,
        draft=draft,
        now=now,
        retention=services.policy.retention,
    )
    if isinstance(plan, Obsolete):
        return Skipped(plan.reason)
    try:
        await ledger.commit(plan.ops)
    except LedgerConflictError:
        return Skipped("job changed underneath")
    return plan.job
