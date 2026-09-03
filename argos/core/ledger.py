"""Decisiones puras sobre el libro de trabajos: cada función devuelve las escrituras
condicionales que la cáscara debe confirmar en una transacción (constitución §7, §9)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from argos.core.messages import JOB_SUBJECTS, failure_subject, success_subject
from argos.core.model import (
    TERMINAL_CASE_STATES,
    Artifact,
    ArtifactState,
    Attempt,
    AttemptState,
    Case,
    CaseState,
    Chunk,
    Document,
    DocumentState,
    Extraction,
    ExtractionState,
    FailureKind,
    Insert,
    Job,
    JobState,
    JobType,
    LedgerOp,
    OutboxEntry,
    OutboxKind,
    OutboxState,
    ReviewState,
    Update,
    attempt_id,
    command_entry_id,
    event_entry_id,
    message_id,
)
from argos.core.policy import JobPolicy, Retention

ATTEMPTS_EXHAUSTED = "job.attempts_exhausted"


@dataclass(frozen=True)
class SubmissionIds:
    case_id: str
    artifact_id: str
    document_id: str
    job_id: str


@dataclass(frozen=True)
class UploadedObject:
    bucket: str
    key: str
    sha256: str
    size: int
    mime: str


@dataclass(frozen=True)
class PlannedSubmission:
    ops: tuple[LedgerOp, ...]
    case_id: str
    document_id: str
    job_id: str


@dataclass(frozen=True)
class SubmissionRefused:
    code: str


def command_entry(job: Job, *, attempt: int, now: datetime, not_before: datetime) -> OutboxEntry:
    return OutboxEntry(
        id=command_entry_id(job.id, attempt),
        tenant_id=job.tenant_id,
        job_id=job.id,
        kind=OutboxKind.COMMAND,
        subject=JOB_SUBJECTS[job.type],
        message_id=message_id(job.id, attempt),
        attempt=attempt,
        state=OutboxState.PENDING,
        not_before=not_before,
        lease_until=None,
        published_at=None,
        created_at=now,
        revision=0,
    )


def event_entry(job: Job, *, subject: str, now: datetime) -> OutboxEntry:
    return OutboxEntry(
        id=event_entry_id(job.id, job.attempt),
        tenant_id=job.tenant_id,
        job_id=job.id,
        kind=OutboxKind.EVENT,
        subject=subject,
        message_id=f"{message_id(job.id, job.attempt)}:{subject}",
        attempt=job.attempt,
        state=OutboxState.PENDING,
        not_before=now,
        lease_until=None,
        published_at=None,
        created_at=now,
        revision=0,
    )


def new_job(
    *,
    job_id: str,
    tenant_id: str,
    case_id: str,
    job_type: JobType,
    document_id: str | None,
    now: datetime,
    policy: JobPolicy,
    extractor_version: str,
    options: str,
    correlation_id: str,
    previous_job_id: str | None = None,
) -> Job:
    return Job(
        id=job_id,
        tenant_id=tenant_id,
        case_id=case_id,
        type=job_type,
        document_id=document_id,
        state=JobState.QUEUED,
        attempt=1,
        max_attempts=policy.max_attempts,
        lease_until=None,
        extractor_version=extractor_version,
        options=options,
        public_error=None,
        internal_error=None,
        correlation_id=correlation_id,
        previous_job_id=previous_job_id,
        created_at=now,
        updated_at=now,
        revision=0,
    )


def staging_artifact(
    *,
    artifact_id: str,
    tenant_id: str,
    case_id: str,
    bucket: str,
    key: str,
    mime: str,
    now: datetime,
    retention: Retention,
) -> Artifact:
    return Artifact(
        id=artifact_id,
        tenant_id=tenant_id,
        case_id=case_id,
        bucket=bucket,
        key=key,
        state=ArtifactState.UPLOADING,
        sha256=None,
        size=0,
        mime=mime,
        created_at=now,
        expires_at=now + retention.staging,
        revision=0,
    )


def _case_for_submission(
    *, tenant_id: str, target: Case | None, ids: SubmissionIds, now: datetime, correlation_id: str
) -> tuple[Case, LedgerOp] | SubmissionRefused:
    if target is None:
        case = Case(
            id=ids.case_id,
            tenant_id=tenant_id,
            state=CaseState.AWAITING_PROCESSING,
            notice_hash=None,
            language=None,
            correlation_id=correlation_id,
            previous_case_id=None,
            review_state=ReviewState.UNREVIEWED,
            reviewed_at=None,
            reviewed_by=None,
            created_at=now,
            updated_at=now,
            revision=0,
        )
        return case, Insert(case)
    if target.tenant_id != tenant_id:
        return SubmissionRefused("case.not_found")
    if target.state is CaseState.ANALYZING:
        return SubmissionRefused("case.busy")
    if target.state in TERMINAL_CASE_STATES:
        linked = Case(
            id=ids.case_id,
            tenant_id=tenant_id,
            state=CaseState.AWAITING_PROCESSING,
            notice_hash=None,
            language=target.language,
            correlation_id=correlation_id,
            previous_case_id=target.id,
            review_state=ReviewState.UNREVIEWED,
            reviewed_at=None,
            reviewed_by=None,
            created_at=now,
            updated_at=now,
            revision=0,
        )
        return linked, Insert(linked)
    waiting = replace(
        target, state=CaseState.AWAITING_PROCESSING, updated_at=now, revision=target.revision + 1
    )
    return waiting, Update(waiting)


def plan_document_submission(
    *,
    tenant_id: str,
    target: Case | None,
    staging: Artifact,
    upload: UploadedObject,
    ids: SubmissionIds,
    now: datetime,
    policy: JobPolicy,
    retention: Retention,
    extractor_version: str,
    options: str,
    correlation_id: str,
) -> PlannedSubmission | SubmissionRefused:
    resolved = _case_for_submission(
        tenant_id=tenant_id, target=target, ids=ids, now=now, correlation_id=correlation_id
    )
    if isinstance(resolved, SubmissionRefused):
        return resolved
    case, case_op = resolved
    artifact = replace(
        staging,
        case_id=case.id,
        state=ArtifactState.AVAILABLE,
        sha256=upload.sha256,
        size=upload.size,
        expires_at=now + retention.full_content,
        revision=staging.revision + 1,
    )
    document = Document(
        id=ids.document_id,
        tenant_id=tenant_id,
        case_id=case.id,
        artifact_id=artifact.id,
        sha256=upload.sha256,
        mime=upload.mime,
        size=upload.size,
        page_count=None,
        state=DocumentState.ACCEPTED,
        created_at=now,
        expires_at=now + retention.full_content,
        revision=0,
    )
    job = new_job(
        job_id=ids.job_id,
        tenant_id=tenant_id,
        case_id=case.id,
        job_type=JobType.DOCUMENT_EXTRACT,
        document_id=document.id,
        now=now,
        policy=policy,
        extractor_version=extractor_version,
        options=options,
        correlation_id=correlation_id,
    )
    ops: tuple[LedgerOp, ...] = (
        case_op,
        Update(artifact),
        Insert(document),
        Insert(job),
        Insert(command_entry(job, attempt=1, now=now, not_before=now)),
    )
    return PlannedSubmission(ops=ops, case_id=case.id, document_id=document.id, job_id=job.id)


@dataclass(frozen=True)
class PlannedNoticeCase:
    ops: tuple[LedgerOp, ...]
    case_id: str
    job_id: str


@dataclass(frozen=True)
class ReusedCase:
    case_id: str


def plan_notice_case(
    *,
    tenant_id: str,
    existing: Case | None,
    notice_hash: str,
    language: str | None,
    case_id: str,
    job_id: str,
    now: datetime,
    policy: JobPolicy,
    correlation_id: str,
) -> PlannedNoticeCase | ReusedCase:
    if existing is not None and existing.state is not CaseState.FAILED:
        return ReusedCase(case_id=existing.id)
    case = Case(
        id=case_id,
        tenant_id=tenant_id,
        state=CaseState.RECEIVED,
        notice_hash=notice_hash,
        language=language,
        correlation_id=correlation_id,
        previous_case_id=None,
        review_state=ReviewState.UNREVIEWED,
        reviewed_at=None,
        reviewed_by=None,
        created_at=now,
        updated_at=now,
        revision=0,
    )
    job = new_job(
        job_id=job_id,
        tenant_id=tenant_id,
        case_id=case.id,
        job_type=JobType.CASE_ANALYZE,
        document_id=None,
        now=now,
        policy=policy,
        extractor_version="",
        options="{}",
        correlation_id=correlation_id,
    )
    return PlannedNoticeCase(
        ops=(
            Insert(case),
            Insert(job),
            Insert(command_entry(job, attempt=1, now=now, not_before=now)),
        ),
        case_id=case.id,
        job_id=job.id,
    )


@dataclass(frozen=True)
class Claimed:
    ops: tuple[LedgerOp, ...]
    job: Job
    attempt: Attempt


@dataclass(frozen=True)
class Obsolete:
    """La entrega no corresponde al intento vigente: se confirma sin efecto (S02 §8)."""

    reason: str


def plan_attempt_claim(
    *, job: Job, attempt_number: int, consumer: str, now: datetime, policy: JobPolicy
) -> Claimed | Obsolete:
    if job.state is not JobState.QUEUED:
        return Obsolete(f"job is {job.state}")
    if job.attempt != attempt_number:
        return Obsolete(f"current attempt is {job.attempt}")
    lease_until = now + policy.lease
    running = replace(
        job,
        state=JobState.RUNNING,
        lease_until=lease_until,
        updated_at=now,
        revision=job.revision + 1,
    )
    attempt = Attempt(
        id=attempt_id(job.id, attempt_number),
        tenant_id=job.tenant_id,
        job_id=job.id,
        number=attempt_number,
        consumer=consumer,
        state=AttemptState.RUNNING,
        started_at=now,
        finished_at=None,
        lease_until=lease_until,
        error_kind=None,
        error_code=None,
        revision=0,
    )
    return Claimed(ops=(Update(running), Insert(attempt)), job=running, attempt=attempt)


def plan_lease_renewal(*, job: Job, now: datetime, policy: JobPolicy) -> tuple[LedgerOp, ...]:
    renewed = replace(
        job, lease_until=now + policy.lease, updated_at=now, revision=job.revision + 1
    )
    return (Update(renewed),)


@dataclass(frozen=True)
class AttemptClosed:
    ops: tuple[LedgerOp, ...]
    job: Job


def is_current_attempt(job: Job, attempt: Attempt) -> bool:
    return (
        job.state is JobState.RUNNING
        and job.attempt == attempt.number
        and attempt.state is AttemptState.RUNNING
    )


def _after_failed_attempt(
    *,
    job: Job,
    closed_attempt: Attempt,
    now: datetime,
    policy: JobPolicy,
    kind: FailureKind,
    code: str,
    document: Document | None,
) -> AttemptClosed:
    ops: list[LedgerOp] = [Update(closed_attempt)]
    exhausted = closed_attempt.number >= job.max_attempts
    if kind is FailureKind.PERMANENT or exhausted:
        public_error = code if kind is FailureKind.PERMANENT else ATTEMPTS_EXHAUSTED
        failed = replace(
            job,
            state=JobState.FAILED,
            lease_until=None,
            public_error=public_error,
            internal_error=code,
            updated_at=now,
            revision=job.revision + 1,
        )
        ops.append(Update(failed))
        ops.append(Insert(event_entry(failed, subject=failure_subject(job.type), now=now)))
        if kind is FailureKind.PERMANENT and document is not None:
            ops.append(
                Update(
                    replace(document, state=DocumentState.REJECTED, revision=document.revision + 1)
                )
            )
        return AttemptClosed(ops=tuple(ops), job=failed)
    next_number = closed_attempt.number + 1
    requeued = replace(
        job,
        state=JobState.QUEUED,
        attempt=next_number,
        lease_until=None,
        internal_error=code,
        updated_at=now,
        revision=job.revision + 1,
    )
    ops.append(Update(requeued))
    ops.append(
        Insert(
            command_entry(
                requeued,
                attempt=next_number,
                now=now,
                not_before=now + policy.backoff(closed_attempt.number),
            )
        )
    )
    return AttemptClosed(ops=tuple(ops), job=requeued)


def plan_attempt_failure(
    *,
    job: Job,
    attempt: Attempt,
    now: datetime,
    policy: JobPolicy,
    kind: FailureKind,
    code: str,
    document: Document | None = None,
) -> AttemptClosed | Obsolete:
    if not is_current_attempt(job, attempt):
        return Obsolete("attempt is not the running one")
    closed = replace(
        attempt,
        state=AttemptState.FAILED,
        finished_at=now,
        error_kind=kind,
        error_code=code,
        revision=attempt.revision + 1,
    )
    return _after_failed_attempt(
        job=job,
        closed_attempt=closed,
        now=now,
        policy=policy,
        kind=kind,
        code=code,
        document=document,
    )


LEASE_LOST = "job.lease_lost"


def plan_lease_recovery(
    *, job: Job, attempt: Attempt, now: datetime, policy: JobPolicy
) -> AttemptClosed | Obsolete:
    if not is_current_attempt(job, attempt):
        return Obsolete("attempt is not the running one")
    if job.lease_until is None or job.lease_until > now:
        return Obsolete("lease is still valid")
    lost = replace(
        attempt,
        state=AttemptState.LOST,
        finished_at=now,
        error_kind=FailureKind.TRANSIENT,
        error_code=LEASE_LOST,
        revision=attempt.revision + 1,
    )
    return _after_failed_attempt(
        job=job,
        closed_attempt=lost,
        now=now,
        policy=policy,
        kind=FailureKind.TRANSIENT,
        code=LEASE_LOST,
        document=None,
    )


@dataclass(frozen=True)
class ChunkDraft:
    page: int
    position: int
    text: str
    sha256: str


@dataclass(frozen=True)
class ExtractionDraft:
    extraction_id: str
    text_artifact: Artifact
    manifest_artifact: Artifact
    sha256: str
    page_count: int
    ocr_pages: int
    chunks: tuple[ChunkDraft, ...]
    chunk_ids: tuple[str, ...]


def plan_extraction_completion(
    *,
    job: Job,
    attempt: Attempt,
    document: Document,
    draft: ExtractionDraft,
    now: datetime,
    retention: Retention,
) -> AttemptClosed | Obsolete:
    if not is_current_attempt(job, attempt):
        return Obsolete("attempt is not the running one")
    if job.document_id != document.id:
        return Obsolete("document does not belong to the job")
    if len(draft.chunk_ids) != len(draft.chunks):
        raise ValueError("one id per chunk is required")
    expires_at = now + retention.full_content
    extraction = Extraction(
        id=draft.extraction_id,
        tenant_id=job.tenant_id,
        case_id=job.case_id,
        document_id=document.id,
        extractor_version=job.extractor_version,
        options=job.options,
        state=ExtractionState.AVAILABLE,
        sha256=draft.sha256,
        page_count=draft.page_count,
        ocr_pages=draft.ocr_pages,
        text_artifact_id=draft.text_artifact.id,
        manifest_artifact_id=draft.manifest_artifact.id,
        created_at=now,
        expires_at=expires_at,
        revision=0,
    )
    succeeded = replace(
        attempt, state=AttemptState.SUCCEEDED, finished_at=now, revision=attempt.revision + 1
    )
    completed = replace(
        job, state=JobState.COMPLETED, lease_until=None, updated_at=now, revision=job.revision + 1
    )
    counted = replace(document, page_count=draft.page_count, revision=document.revision + 1)
    ops: list[LedgerOp] = [
        Insert(draft.text_artifact),
        Insert(draft.manifest_artifact),
        Insert(extraction),
    ]
    ops.extend(
        Insert(
            Chunk(
                id=chunk_id,
                tenant_id=job.tenant_id,
                extraction_id=extraction.id,
                page=chunk.page,
                position=chunk.position,
                text=chunk.text,
                sha256=chunk.sha256,
                expires_at=expires_at,
                revision=0,
            )
        )
        for chunk, chunk_id in zip(draft.chunks, draft.chunk_ids, strict=True)
    )
    ops.extend(
        (
            Update(counted),
            Update(succeeded),
            Update(completed),
            Insert(event_entry(completed, subject=success_subject(job.type), now=now)),
        )
    )
    return AttemptClosed(ops=tuple(ops), job=completed)


def plan_outbox_claim(entry: OutboxEntry, *, now: datetime, policy: JobPolicy) -> OutboxEntry:
    return replace(entry, lease_until=now + policy.outbox_lease, revision=entry.revision + 1)


def plan_outbox_release(entry: OutboxEntry) -> OutboxEntry:
    return replace(entry, lease_until=None, revision=entry.revision + 1)


def plan_outbox_published(entry: OutboxEntry, *, now: datetime) -> OutboxEntry:
    return replace(
        entry,
        state=OutboxState.PUBLISHED,
        published_at=now,
        lease_until=None,
        revision=entry.revision + 1,
    )


def is_outbox_deliverable(entry: OutboxEntry, *, now: datetime) -> bool:
    return (
        entry.state is OutboxState.PENDING
        and entry.not_before <= now
        and (entry.lease_until is None or entry.lease_until <= now)
    )
