"""Capacidades de Argos. Cada una es código determinista: valida, analiza y
devuelve estado."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from argos.core.analysis import verdict_language
from argos.core.model import Case, CaseState, ReviewState
from argos.core.notices import Notice
from argos.core.ports import CaseAdvisor, ConversationBrief, Investigator, Narrator
from argos.core.reports import NO_VERDICT_YET
from argos.core.verdicts import plan_review
from argos.usecases.analysis import Failed, analyze_case
from argos.usecases.deps import Bookkeeping
from argos.usecases.notices import NoticeRefused, open_notice_case
from argos.usecases.queries import CaseView, get_case

type Analysts = tuple[Investigator, Narrator]
type Investigators = Callable[[str], Analysts]


@dataclass(frozen=True)
class AnalysisFailed:
    case_id: str
    error: str


async def analyze_notice(
    services: Bookkeeping, investigators: Investigators, notice: Notice
) -> CaseView | NoticeRefused | AnalysisFailed:
    """El aviso se analiza en la misma llamada: no hay nada que reanudar."""
    opened = await open_notice_case(services, notice)
    if isinstance(opened, NoticeRefused):
        return opened
    investigator, narrator = investigators(opened.id)
    outcome = await analyze_case(services, investigator, narrator, case_id=opened.id)
    if isinstance(outcome, Failed):
        return AnalysisFailed(case_id=opened.id, error=outcome.error)
    view = await get_case(services, opened.id)
    if view is None:
        return AnalysisFailed(case_id=opened.id, error="el caso desapareció durante el análisis")
    return view


@dataclass(frozen=True)
class CaseAnswer:
    case_id: str
    answer: str
    view: CaseView


async def ask_case(
    services: Bookkeeping, advisor: CaseAdvisor, *, case_id: str, question: str
) -> CaseAnswer | None:
    view = await get_case(services, case_id)
    if view is None:
        return None
    if view.verdict is None:
        return CaseAnswer(case_id=case_id, answer=NO_VERDICT_YET, view=view)
    case = await services.ledger.case(case_id)
    answer = await advisor.answer(
        ConversationBrief(
            case_id=case_id,
            language=verdict_language(case.language if case else None),
            question=question,
            level=view.verdict.level,
            outcome=view.verdict.outcome,
            summary=view.verdict.summary,
            actions=view.verdict.actions,
            quotes=tuple(signal.quote for signal in view.signals),
        )
    )
    return CaseAnswer(case_id=case_id, answer=answer, view=view)


async def review_case(
    services: Bookkeeping, *, case_id: str, review: ReviewState
) -> Case | None:
    """R13: marcar un caso confirmado es lo que hace visible la reincidencia después."""
    case = await services.ledger.case(case_id)
    if case is None or case.state is CaseState.ANALYZING:
        return None
    reviewed = plan_review(case=case, review=review, now=services.clock.now())
    await services.ledger.commit(reviewed.ops)
    return reviewed.case
