"""W5 · Ingreso de un documento (S02 §9)."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass

from argos.core.documents import PDF_SIGNATURE, UploadRejected, validate_upload
from argos.core.keys import source_document_key
from argos.core.ledger import (
    PlannedSubmission,
    SubmissionIds,
    SubmissionRefused,
    UploadedObject,
    plan_document_submission,
    staging_artifact,
)
from argos.core.model import (
    TERMINAL_CASE_STATES,
    Case,
    CaseState,
    Document,
    Insert,
    Job,
    JobState,
    JobType,
)
from argos.core.ports import LedgerConflictError, ObjectSizeMismatchError
from argos.usecases.deps import Services
from argos.usecases.streams import UploadTooLargeError, bounded, peek


@dataclass(frozen=True)
class DocumentUpload:
    tenant_id: str
    case_id: str | None
    filename: str
    declared_mime: str
    size: int
    content: AsyncIterable[bytes]
    correlation_id: str


@dataclass(frozen=True)
class DocumentAccepted:
    case_id: str
    document_id: str
    job_id: str
    job_state: JobState
    reused: bool


@dataclass(frozen=True)
class DocumentRejected:
    code: str


def submission_case_id(target: Case | None, new_case_id: str) -> str | SubmissionRefused:
    if target is None or target.state in TERMINAL_CASE_STATES:
        return new_case_id
    if target.state is CaseState.ANALYZING:
        return SubmissionRefused("case.busy")
    return target.id


async def _extract_job_of(services: Services, document: Document) -> Job | None:
    jobs = [
        job
        for job in await services.ledger.jobs_of_case(document.case_id)
        if job.type is JobType.DOCUMENT_EXTRACT and job.document_id == document.id
    ]
    return jobs[-1] if jobs else None


async def _reuse(services: Services, document: Document) -> DocumentAccepted | DocumentRejected:
    job = await _extract_job_of(services, document)
    if job is None:
        return DocumentRejected("document.inconsistent")
    return DocumentAccepted(
        case_id=document.case_id,
        document_id=document.id,
        job_id=job.id,
        job_state=job.state,
        reused=True,
    )


async def submit_document(
    services: Services, upload: DocumentUpload
) -> DocumentAccepted | DocumentRejected:
    ledger = services.ledger
    tenant = await ledger.tenant(upload.tenant_id)
    if tenant is None or not tenant.active:
        return DocumentRejected("tenant.unknown")
    target: Case | None = None
    if upload.case_id is not None:
        target = await ledger.case(upload.case_id)
        if target is None or target.tenant_id != upload.tenant_id:
            return DocumentRejected("case.not_found")

    head, content = await peek(upload.content, len(PDF_SIGNATURE))
    checked = validate_upload(
        filename=upload.filename,
        declared_mime=upload.declared_mime,
        head=head,
        size=upload.size,
        limits=services.policy.documents,
    )
    if isinstance(checked, UploadRejected):
        return DocumentRejected(checked.code)

    ids = SubmissionIds(
        case_id=services.ids.new_id(),
        artifact_id=services.ids.new_id(),
        document_id=services.ids.new_id(),
        job_id=services.ids.new_id(),
    )
    resolved = submission_case_id(target, ids.case_id)
    if isinstance(resolved, SubmissionRefused):
        return DocumentRejected(resolved.code)
    case_id = resolved
    now = services.clock.now()
    key = source_document_key(upload.tenant_id, case_id, ids.document_id)
    staging = staging_artifact(
        artifact_id=ids.artifact_id,
        tenant_id=upload.tenant_id,
        case_id=case_id,
        bucket=services.bucket,
        key=key,
        mime=checked.mime,
        now=now,
        retention=services.policy.retention,
    )
    await ledger.commit([Insert(staging)])

    try:
        stored = await services.object_store.put(
            key,
            bounded(content, services.policy.documents.max_bytes),
            size=upload.size,
            mime=checked.mime,
        )
    except UploadTooLargeError:
        await services.object_store.delete(key)
        return DocumentRejected("document.too_large")
    except ObjectSizeMismatchError:
        await services.object_store.delete(key)
        return DocumentRejected("document.size_mismatch")

    existing = await ledger.document_by_hash(case_id, stored.sha256)
    if existing is not None:
        return await _reuse(services, existing)

    plan = plan_document_submission(
        tenant_id=upload.tenant_id,
        target=target,
        staging=staging,
        upload=UploadedObject(
            bucket=services.bucket,
            key=key,
            sha256=stored.sha256,
            size=stored.size,
            mime=checked.mime,
        ),
        ids=ids,
        now=services.clock.now(),
        policy=services.policy.jobs,
        retention=services.policy.retention,
        extractor_version=services.policy.extractor_version,
        options=services.policy.extraction_options,
        correlation_id=upload.correlation_id,
    )
    if isinstance(plan, SubmissionRefused):
        return DocumentRejected(plan.code)
    return await _commit_submission(services, plan, case_id=case_id, sha256=stored.sha256)


async def _commit_submission(
    services: Services, plan: PlannedSubmission, *, case_id: str, sha256: str
) -> DocumentAccepted | DocumentRejected:
    try:
        await services.ledger.commit(plan.ops)
    except LedgerConflictError:
        existing = await services.ledger.document_by_hash(case_id, sha256)
        if existing is None:
            raise
        return await _reuse(services, existing)
    return DocumentAccepted(
        case_id=plan.case_id,
        document_id=plan.document_id,
        job_id=plan.job_id,
        job_state=JobState.QUEUED,
        reused=False,
    )
