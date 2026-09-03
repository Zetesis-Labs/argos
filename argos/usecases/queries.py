"""Consultas públicas acotadas por tenant (R16): nunca devuelven detalle interno."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.model import (
    CaseState,
    DocumentState,
    JobState,
    JobType,
    ReviewState,
    RiskLevel,
    Verdict,
    VerdictOutcome,
)
from argos.usecases.deps import Bookkeeping


@dataclass(frozen=True)
class JobView:
    id: str
    case_id: str
    document_id: str | None
    type: JobType
    state: JobState
    attempt: int
    public_error: str | None


@dataclass(frozen=True)
class VerdictSummary:
    version: int
    level: RiskLevel
    outcome: VerdictOutcome
    summary: str
    actions: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class CaseView:
    id: str
    state: CaseState
    previous_case_id: str | None
    review_state: ReviewState
    verdict: VerdictSummary | None


def summary_of(verdict: Verdict | None) -> VerdictSummary | None:
    if verdict is None:
        return None
    return VerdictSummary(
        version=verdict.version,
        level=verdict.level,
        outcome=verdict.outcome,
        summary=verdict.summary,
        actions=verdict.actions,
        missing=verdict.missing,
    )


@dataclass(frozen=True)
class DocumentView:
    id: str
    case_id: str
    state: DocumentState
    size: int
    page_count: int | None


async def get_job(services: Bookkeeping, *, tenant_id: str, job_id: str) -> JobView | None:
    job = await services.ledger.job(job_id)
    if job is None or job.tenant_id != tenant_id:
        return None
    return JobView(
        id=job.id,
        case_id=job.case_id,
        document_id=job.document_id,
        type=job.type,
        state=job.state,
        attempt=job.attempt,
        public_error=job.public_error,
    )


async def get_case(services: Bookkeeping, *, tenant_id: str, case_id: str) -> CaseView | None:
    case = await services.ledger.case(case_id)
    if case is None or case.tenant_id != tenant_id:
        return None
    return CaseView(
        id=case.id,
        state=case.state,
        previous_case_id=case.previous_case_id,
        review_state=case.review_state,
        verdict=summary_of(await services.ledger.current_verdict(case.id)),
    )


async def get_document(
    services: Bookkeeping, *, tenant_id: str, document_id: str
) -> DocumentView | None:
    document = await services.ledger.document(document_id)
    if document is None or document.tenant_id != tenant_id:
        return None
    return DocumentView(
        id=document.id,
        case_id=document.case_id,
        state=document.state,
        size=document.size,
        page_count=document.page_count,
    )
