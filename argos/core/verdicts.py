"""Plan del análisis de un caso: arranque, memoria compartida y veredicto durable."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from argos.core.analysis import Assessment, DraftEntity, DraftSignal
from argos.core.ledger import Obsolete, entity_memory_ops
from argos.core.model import (
    TERMINAL_CASE_STATES,
    Case,
    CaseState,
    Entity,
    Insert,
    LedgerOp,
    ReviewState,
    Signal,
    Update,
    Verdict,
    VerdictOutcome,
    VerdictState,
    verdict_id,
)

CASE_STATE_OF_OUTCOME: dict[VerdictOutcome, CaseState] = {
    VerdictOutcome.ISSUED: CaseState.VERDICT_ISSUED,
    VerdictOutcome.PARTIAL: CaseState.PARTIAL,
    VerdictOutcome.INSUFFICIENT: CaseState.INSUFFICIENT,
}


@dataclass(frozen=True)
class VerdictDraft:
    assessment: Assessment
    signals: tuple[DraftSignal, ...]
    entities: tuple[DraftEntity, ...]
    language: str
    summary: str


@dataclass(frozen=True)
class AnalysisStarted:
    ops: tuple[LedgerOp, ...]
    case: Case


@dataclass(frozen=True)
class AnalysisClosed:
    ops: tuple[LedgerOp, ...]
    case: Case
    verdict: Verdict


def plan_analysis_start(*, case: Case, now: datetime) -> AnalysisStarted | Obsolete:
    """Marcar `analyzing` con revisión condicional es lo que impide dos análisis a la vez."""
    if case.state in TERMINAL_CASE_STATES:
        return Obsolete(f"case is {case.state}")
    if case.state is CaseState.ANALYZING:
        return Obsolete("case is already being analyzed")
    analyzing = replace(case, state=CaseState.ANALYZING, updated_at=now, revision=case.revision + 1)
    return AnalysisStarted(ops=(Update(analyzing),), case=analyzing)


def _signal_ops(
    *, case: Case, signals: Sequence[DraftSignal], ids: Sequence[str], now: datetime
) -> tuple[LedgerOp, ...]:
    ops: list[LedgerOp] = []
    for drafted, identifier in zip(signals, ids, strict=True):
        evidence = drafted.evidence
        if evidence.observed_at is None:
            raise ValueError("a signal without an observation date cannot be stored")
        ops.append(
            Insert(
                Signal(
                    id=identifier,
                    case_id=case.id,
                    analysis=drafted.analysis,
                    code=drafted.code,
                    strength=drafted.strength,
                    official=drafted.official,
                    recidivism=drafted.recidivism,
                    source=evidence.source,
                    observed_at=evidence.observed_at,
                    value=evidence.value,
                    quote=evidence.quote,
                    created_at=now,
                    revision=0,
                )
            )
        )
    return tuple(ops)


def plan_analysis_completion(
    *,
    case: Case,
    draft: VerdictDraft,
    known: Mapping[str, Entity],
    linked: frozenset[str],
    previous: Verdict | None,
    signal_ids: Sequence[str],
    now: datetime,
) -> AnalysisClosed | Obsolete:
    if case.state is not CaseState.ANALYZING:
        return Obsolete(f"case is {case.state}")
    version = previous.version + 1 if previous is not None else 1
    verdict = Verdict(
        id=verdict_id(case.id, version),
        case_id=case.id,
        version=version,
        level=draft.assessment.level,
        outcome=draft.assessment.outcome,
        state=VerdictState.CURRENT,
        language=draft.language,
        summary=draft.summary,
        actions=draft.assessment.actions,
        missing=draft.assessment.missing,
        created_at=now,
        revision=0,
    )
    closed = replace(
        case,
        state=CASE_STATE_OF_OUTCOME[draft.assessment.outcome],
        updated_at=now,
        revision=case.revision + 1,
    )
    ops: list[LedgerOp] = list(
        entity_memory_ops(case=case, entities=draft.entities, known=known, linked=linked, now=now)
    )
    ops.extend(_signal_ops(case=case, signals=draft.signals, ids=signal_ids, now=now))
    if previous is not None:
        ops.append(
            Update(replace(previous, state=VerdictState.SUPERSEDED, revision=previous.revision + 1))
        )
    ops.extend((Insert(verdict), Update(closed)))
    return AnalysisClosed(ops=tuple(ops), case=closed, verdict=verdict)


def plan_analysis_failure(*, case: Case, error: str, now: datetime) -> tuple[LedgerOp, ...]:
    """Un análisis que revienta deja el caso operable, nunca colgado en `analyzing`."""
    failed = replace(
        case, state=CaseState.FAILED, error=error, updated_at=now, revision=case.revision + 1
    )
    return (Update(failed),)


@dataclass(frozen=True)
class Reviewed:
    ops: tuple[LedgerOp, ...]
    case: Case


def plan_review(*, case: Case, review: ReviewState, now: datetime) -> Reviewed:
    """Confirmar un caso es lo que da sentido a la memoria: sin revisión, la
    reincidencia nunca se puede afirmar (R13)."""
    marked = replace(
        case,
        review_state=review,
        reviewed_at=now,
        updated_at=now,
        revision=case.revision + 1,
    )
    return Reviewed(ops=(Update(marked),), case=marked)
