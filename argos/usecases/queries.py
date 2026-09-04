"""Lo que se puede leer de un caso: su estado, su veredicto y su evidencia."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.model import (
    Analysis,
    CaseState,
    ReviewState,
    RiskLevel,
    Signal,
    Strength,
    Verdict,
    VerdictOutcome,
)
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
class SignalView:
    """La evidencia que sostiene el nivel: sin ella el veredicto es un número."""

    analysis: Analysis
    code: str
    strength: Strength
    official: bool
    recidivism: bool
    source: str
    quote: str


@dataclass(frozen=True)
class CaseView:
    id: str
    state: CaseState
    review_state: ReviewState
    error: str | None
    verdict: VerdictSummary | None
    signals: tuple[SignalView, ...]


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


def signal_view_of(signal: Signal) -> SignalView:
    return SignalView(
        analysis=signal.analysis,
        code=signal.code,
        strength=signal.strength,
        official=signal.official,
        recidivism=signal.recidivism,
        source=signal.source,
        quote=signal.quote,
    )


async def get_case(services: Bookkeeping, case_id: str) -> CaseView | None:
    case = await services.ledger.case(case_id)
    if case is None:
        return None
    return CaseView(
        id=case.id,
        state=case.state,
        review_state=case.review_state,
        error=case.error,
        verdict=summary_of(await services.ledger.current_verdict(case.id)),
        signals=tuple(
            signal_view_of(signal) for signal in await services.ledger.signals_of_case(case.id)
        ),
    )
