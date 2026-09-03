"""Reanudación del caso (S02 §9): el evento solo trae referencias; el estado se
relee de SurrealDB y el trabajo de análisis nace con su comando en una transacción."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.messages import JobMessage
from argos.core.ports import LedgerConflictError
from argos.core.verdicts import AnalysisNotDue, plan_case_analysis
from argos.usecases.consumers import Skipped
from argos.usecases.deps import Bookkeeping


@dataclass(frozen=True)
class AnalysisQueued:
    case_id: str
    job_id: str


async def resume_case(services: Bookkeeping, message: JobMessage) -> AnalysisQueued | Skipped:
    ledger = services.ledger
    job = await ledger.job(message.job_id)
    if job is None:
        return Skipped("unknown job")
    case = await ledger.case(job.case_id)
    if case is None or case.tenant_id != job.tenant_id:
        return Skipped("unknown case")
    plan = plan_case_analysis(
        case=case,
        jobs=await ledger.jobs_of_case(case.id),
        now=services.clock.now(),
        policy=services.policy.jobs,
        correlation_id=case.correlation_id,
    )
    if isinstance(plan, AnalysisNotDue):
        return Skipped(plan.reason)
    try:
        await ledger.commit(plan.ops)
    except LedgerConflictError:
        return Skipped("analysis already created")
    return AnalysisQueued(case_id=case.id, job_id=plan.job_id)
