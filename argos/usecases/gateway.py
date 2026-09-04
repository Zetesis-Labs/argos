"""Capacidades del gateway. Cada una es código determinista: valida, resuelve el
tenant que ya trae la identidad y devuelve estado, nunca topología."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from argos.core.analysis import verdict_language
from argos.core.model import CaseState
from argos.core.notices import Notice
from argos.core.ports import CaseAdvisor, ConversationBrief, Investigator, Narrator
from argos.core.reports import NO_VERDICT_YET
from argos.usecases.analysis import analyze_case
from argos.usecases.deps import Bookkeeping
from argos.usecases.notices import NoticeRefused, open_notice_case
from argos.usecases.queries import VerdictSummary, get_case, summary_of

type Analysts = tuple[Investigator, Narrator]
type Investigators = Callable[[str, str], Analysts]


@dataclass(frozen=True)
class NoticeAnalysis:
    case_id: str
    state: CaseState
    verdict: VerdictSummary | None
    reused: bool


async def analyze_notice(
    services: Bookkeeping,
    investigators: Investigators,
    *,
    tenant_id: str,
    notice: Notice,
    correlation_id: str,
) -> NoticeAnalysis | NoticeRefused:
    """El aviso breve se analiza en la misma llamada: no hay nada que reanudar."""
    opened = await open_notice_case(
        services, tenant_id=tenant_id, notice=notice, correlation_id=correlation_id
    )
    if isinstance(opened, NoticeRefused):
        return opened
    if not opened.reused:
        investigator, narrator = investigators(tenant_id, opened.case.id)
        await analyze_case(services, investigator, narrator, case_id=opened.case.id)
    settled = await services.ledger.case(opened.case.id)
    return NoticeAnalysis(
        case_id=opened.case.id,
        state=settled.state if settled is not None else CaseState.RECEIVED,
        verdict=summary_of(await services.ledger.current_verdict(opened.case.id)),
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
