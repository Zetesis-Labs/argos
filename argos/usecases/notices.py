"""W1 · Apertura del caso de un aviso breve: caso y trabajo `case.analyze` en una transacción."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.ledger import ReusedCase, plan_notice_case
from argos.core.model import JobType
from argos.core.notices import Notice, NoticeRejected, validate_notice
from argos.core.ports import LedgerConflictError
from argos.usecases.deps import Bookkeeping


@dataclass(frozen=True)
class NoticeOpened:
    case_id: str
    job_id: str
    reused: bool


@dataclass(frozen=True)
class NoticeRefused:
    code: str


async def _analysis_job_id(services: Bookkeeping, case_id: str) -> str | None:
    jobs = [
        job
        for job in await services.ledger.jobs_of_case(case_id)
        if job.type is JobType.CASE_ANALYZE
    ]
    return jobs[-1].id if jobs else None


async def open_notice_case(
    services: Bookkeeping, *, tenant_id: str, notice: Notice, correlation_id: str
) -> NoticeOpened | NoticeRefused:
    ledger = services.ledger
    tenant = await ledger.tenant(tenant_id)
    if tenant is None or not tenant.active:
        return NoticeRefused("tenant.unknown")
    checked = validate_notice(notice, services.policy.notices)
    if isinstance(checked, NoticeRejected):
        return NoticeRefused(checked.code)
    now = services.clock.now()
    since = now - services.policy.retention.notice_dedup_window
    existing = await ledger.case_by_notice(tenant_id, checked.notice_hash, since=since)
    plan = plan_notice_case(
        tenant_id=tenant_id,
        existing=existing,
        notice_hash=checked.notice_hash,
        language=notice.language_hint,
        case_id=services.ids.new_id(),
        job_id=services.ids.new_id(),
        now=now,
        policy=services.policy.jobs,
        correlation_id=correlation_id,
    )
    if isinstance(plan, ReusedCase):
        job_id = await _analysis_job_id(services, plan.case_id)
        if job_id is None:
            return NoticeRefused("case.inconsistent")
        return NoticeOpened(case_id=plan.case_id, job_id=job_id, reused=True)
    try:
        await ledger.commit(plan.ops)
    except LedgerConflictError:
        raced = await ledger.case_by_notice(tenant_id, checked.notice_hash, since=since)
        if raced is None:
            raise
        job_id = await _analysis_job_id(services, raced.id)
        if job_id is None:
            return NoticeRefused("case.inconsistent")
        return NoticeOpened(case_id=raced.id, job_id=job_id, reused=True)
    return NoticeOpened(case_id=plan.case_id, job_id=plan.job_id, reused=False)
