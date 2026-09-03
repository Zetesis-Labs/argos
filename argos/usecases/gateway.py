"""Capacidades del gateway (S02 §5). Cada una es código determinista: valida,
resuelve el tenant que ya trae la identidad y devuelve estado, nunca topología."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from argos.core.analysis import verdict_language
from argos.core.model import TERMINAL_CASE_STATES, Case, CaseState
from argos.core.notices import Notice
from argos.core.ports import CaseAdvisor, ConversationBrief, LedgerConflictError
from argos.core.reports import NO_VERDICT_YET
from argos.core.reprocess import ReprocessRefused, plan_reprocess
from argos.usecases.deps import Bookkeeping
from argos.usecases.notices import NoticeRefused, open_notice_case
from argos.usecases.queries import VerdictSummary, get_case, summary_of

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class NoticeAnalysis:
    case_id: str
    job_id: str
    state: CaseState
    verdict: VerdictSummary | None
    reused: bool

    @property
    def settled(self) -> bool:
        return self.state in TERMINAL_CASE_STATES


async def wait_for_case(
    services: Bookkeeping, case_id: str, *, sleep: Sleep, budget: timedelta, poll: float
) -> Case | None:
    """R15: se espera el estado terminal hasta el presupuesto, nunca más."""
    deadline = services.clock.now() + budget
    while True:
        case = await services.ledger.case(case_id)
        if case is None or case.state in TERMINAL_CASE_STATES:
            return case
        if services.clock.now() >= deadline:
            return case
        await sleep(poll)


async def analyze_notice(
    services: Bookkeeping,
    *,
    tenant_id: str,
    notice: Notice,
    correlation_id: str,
    sleep: Sleep,
    poll: float = 0.2,
) -> NoticeAnalysis | NoticeRefused:
    opened = await open_notice_case(
        services, tenant_id=tenant_id, notice=notice, correlation_id=correlation_id
    )
    if isinstance(opened, NoticeRefused):
        return opened
    settled = await wait_for_case(
        services, opened.case_id, sleep=sleep, budget=services.policy.analysis.budget, poll=poll
    )
    state = settled.state if settled is not None else CaseState.RECEIVED
    return NoticeAnalysis(
        case_id=opened.case_id,
        job_id=opened.job_id,
        state=state,
        verdict=summary_of(await services.ledger.current_verdict(opened.case_id)),
        reused=opened.reused,
    )


@dataclass(frozen=True)
class CaseAnswer:
    case_id: str
    answer: str
    verdict: VerdictSummary | None


async def ask_case(
    services: Bookkeeping,
    advisor: CaseAdvisor,
    *,
    tenant_id: str,
    case_id: str,
    question: str,
) -> CaseAnswer | None:
    view = await get_case(services, tenant_id=tenant_id, case_id=case_id)
    if view is None:
        return None
    if view.verdict is None:
        return CaseAnswer(case_id=case_id, answer=NO_VERDICT_YET, verdict=None)
    case = await services.ledger.case(case_id)
    signals = await services.ledger.signals_of_case(case_id)
    answer = await advisor.answer(
        ConversationBrief(
            case_id=case_id,
            language=verdict_language(case.language if case else None),
            question=question,
            level=view.verdict.level,
            outcome=view.verdict.outcome,
            summary=view.verdict.summary,
            actions=view.verdict.actions,
            quotes=tuple(signal.quote for signal in signals),
        )
    )
    return CaseAnswer(case_id=case_id, answer=answer, verdict=view.verdict)


@dataclass(frozen=True)
class Reprocessed:
    case_id: str
    document_id: str
    job_id: str
    options: str


async def reprocess_document(
    services: Bookkeeping, *, document_id: str, correlation_id: str
) -> Reprocessed | ReprocessRefused:
    """Del curador: cruza tenants por diseño y queda atribuido (constitución §6)."""
    ledger = services.ledger
    document = await ledger.document(document_id)
    if document is None:
        return ReprocessRefused("document.not_found")
    case = await ledger.case(document.case_id)
    if case is None:
        return ReprocessRefused("case.not_found")
    plan = plan_reprocess(
        case=case,
        document=document,
        jobs=await ledger.jobs_of_case(case.id),
        job_id=services.ids.new_id(),
        now=services.clock.now(),
        policy=services.policy.jobs,
        extractor_version=services.policy.extractor_version,
        correlation_id=correlation_id,
    )
    if isinstance(plan, ReprocessRefused):
        return plan
    try:
        await ledger.commit(plan.ops)
    except LedgerConflictError:
        return ReprocessRefused("job.busy")
    return Reprocessed(
        case_id=case.id, document_id=document.id, job_id=plan.job_id, options=plan.options
    )
