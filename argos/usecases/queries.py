"""Consultas públicas acotadas por tenant (R16): nunca devuelven detalle interno."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.model import CaseState, ReviewState, RiskLevel, Verdict, VerdictOutcome
from argos.usecases.deps import Bookkeeping


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
    public_error: str | None
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


async def get_case(services: Bookkeeping, *, tenant_id: str, case_id: str) -> CaseView | None:
    case = await services.ledger.case(case_id)
    if case is None or case.tenant_id != tenant_id:
        return None
    return CaseView(
        id=case.id,
        state=case.state,
        previous_case_id=case.previous_case_id,
        review_state=case.review_state,
        public_error=case.public_error,
        verdict=summary_of(await services.ledger.current_verdict(case.id)),
    )
