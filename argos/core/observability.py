"""Observabilidad sin contenido (constitución §11; S02 §13). Aquí se decide qué
sale a una traza, a una métrica y a un error público: identificadores y códigos."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from argos.core.ledger import ATTEMPTS_EXHAUSTED, LEASE_LOST
from argos.core.model import (
    Attempt,
    CaseState,
    Job,
    JobCount,
    JobState,
    OutboxEntry,
    OutboxState,
    Verdict,
)
from argos.core.ports import (
    ObjectSizeMismatchError,
    ObjectTooLargeError,
    PdfDamagedError,
    PdfEncryptedError,
    PdfError,
    PdfTooManyPagesError,
)

INTERNAL_ERROR = "job.internal_error"

PUBLIC_ERRORS = frozenset(
    {
        ATTEMPTS_EXHAUSTED,
        LEASE_LOST,
        INTERNAL_ERROR,
        PdfError.code,
        PdfDamagedError.code,
        PdfEncryptedError.code,
        PdfTooManyPagesError.code,
        "document.hash_mismatch",
        "document.not_pdf",
        "document.bad_extension",
        "document.bad_mime",
        "document.too_large",
        "document.missing",
        "notice.text_too_long",
        "notice.too_many_links",
        "notice.image_too_large",
        "notice.image_unsupported",
        "notice.empty",
        "case.analysis_failed",
        "case.busy",
        "case.not_found",
        "job.busy",
        "worker.unexpected",
        "object.too_large",
        "object.size_mismatch",
    }
)

Attributes = dict[str, str | int]


def public_code(code: str | None) -> str:
    """Lo que no está en el catálogo no sale: el detalle se queda en el libro."""
    return code if code in PUBLIC_ERRORS else INTERNAL_ERROR


def error_code_of(error: BaseException) -> str:
    match error:
        case PdfError():
            return error.code
        case ObjectTooLargeError():
            return "object.too_large"
        case ObjectSizeMismatchError():
            return "object.size_mismatch"
        case _:
            return INTERNAL_ERROR


def job_attributes(job: Job) -> Attributes:
    attributes: Attributes = {
        "argos.tenant_id": job.tenant_id,
        "argos.case_id": job.case_id,
        "argos.job_id": job.id,
        "argos.job_type": str(job.type),
        "argos.job_state": str(job.state),
        "argos.attempt": job.attempt,
        "argos.correlation_id": job.correlation_id,
        "argos.extractor_version": job.extractor_version,
    }
    if job.document_id is not None:
        attributes["argos.document_id"] = job.document_id
    if job.public_error is not None:
        attributes["argos.public_error"] = public_code(job.public_error)
    return attributes


def attempt_attributes(attempt: Attempt) -> Attributes:
    return {
        "argos.attempt": attempt.number,
        "argos.attempt_state": str(attempt.state),
        "argos.consumer": attempt.consumer,
    }


def extraction_attributes(
    *, pages: int, ocr_pages: int, chunks: int, bytes_read: int
) -> Attributes:
    return {
        "argos.pages": pages,
        "argos.ocr_pages": ocr_pages,
        "argos.chunks": chunks,
        "argos.bytes": bytes_read,
    }


def verdict_attributes(verdict: Verdict) -> Attributes:
    return {
        "argos.verdict_version": verdict.version,
        "argos.level": str(verdict.level),
        "argos.outcome": str(verdict.outcome),
        "argos.missing": len(verdict.missing),
    }


@dataclass(frozen=True)
class Metrics:
    """Las mínimas de S02 §13. Ninguna lleva identificadores de caso ni contenido."""

    jobs: Mapping[str, int]
    oldest_queued: timedelta | None
    pending_outbox: int
    awaiting_documents: int
    failed: int

    def as_dict(self) -> dict[str, object]:
        return {
            "jobs": dict(sorted(self.jobs.items())),
            "oldest_queued_seconds": (
                None if self.oldest_queued is None else int(self.oldest_queued.total_seconds())
            ),
            "pending_outbox": self.pending_outbox,
            "awaiting_documents": self.awaiting_documents,
            "failed": self.failed,
        }


def summarize(
    *,
    counts: Sequence[JobCount],
    oldest_queued_at: datetime | None,
    outbox: Sequence[OutboxEntry],
    awaiting: int,
    now: datetime,
) -> Metrics:
    return Metrics(
        jobs={f"{count.type}.{count.state}": count.count for count in counts},
        oldest_queued=None if oldest_queued_at is None else now - oldest_queued_at,
        pending_outbox=sum(1 for entry in outbox if entry.state is OutboxState.PENDING),
        awaiting_documents=awaiting,
        failed=sum(count.count for count in counts if count.state is JobState.FAILED),
    )


AWAITING_STATES = (CaseState.AWAITING_PROCESSING, CaseState.RECEIVED)
