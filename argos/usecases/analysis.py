"""Análisis del caso: mueve el estado, reúne señales y cierra con un veredicto
durable. Las transiciones y el nivel son código, nunca un prompt."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from argos.core.analysis import DraftSignal, assess, usable, verdict_language
from argos.core.identifiers import extract_identifiers
from argos.core.ledger import Obsolete
from argos.core.model import Case, Verdict
from argos.core.notices import normalize_text
from argos.core.ports import CaseBrief, Investigator, LedgerConflictError, Narrator, VerdictBrief
from argos.core.reports import quote_is_literal
from argos.core.verdicts import (
    VerdictDraft,
    plan_analysis_completion,
    plan_analysis_failure,
    plan_analysis_start,
)
from argos.usecases.deps import Bookkeeping
from argos.usecases.notices import known_entities
from argos.usecases.signals import official_signals, recidivism_signals

BUDGET_EXHAUSTED = "el análisis agotó su presupuesto de tiempo"


@dataclass(frozen=True)
class Analyzed:
    case: Case
    verdict: Verdict


@dataclass(frozen=True)
class Skipped:
    reason: str


@dataclass(frozen=True)
class Failed:
    case: Case
    error: str


def build_brief(case: Case) -> CaseBrief:
    return CaseBrief(
        case_id=case.id,
        language=verdict_language(case.language),
        text=case.notice_text,
        links=case.notice_links,
        entities=extract_identifiers(case.notice_text, case.notice_links),
    )


def grounded(signals: tuple[DraftSignal, ...], text: str) -> tuple[DraftSignal, ...]:
    """Una cita inventada no sostiene una señal: se descarta antes de puntuar (R3)."""
    return tuple(signal for signal in signals if quote_is_literal(signal.evidence.quote, text))


async def analyze_case(
    services: Bookkeeping, investigator: Investigator, narrator: Narrator, *, case_id: str
) -> Analyzed | Skipped | Failed:
    """Un fallo deja el caso `failed` con su error, nunca colgado en `analyzing`."""
    ledger = services.ledger
    case = await ledger.case(case_id)
    if case is None:
        return Skipped("unknown case")
    started = plan_analysis_start(case=case, now=services.clock.now())
    if isinstance(started, Obsolete):
        return Skipped(started.reason)
    try:
        await ledger.commit(started.ops)
    except LedgerConflictError:
        return Skipped("case changed underneath")
    try:
        async with asyncio.timeout(services.policy.analysis.budget.total_seconds()):
            return await _analyze(services, investigator, narrator, case=started.case)
    except TimeoutError:
        return await _fail(services, started.case, BUDGET_EXHAUSTED)
    except Exception as error:
        return await _fail(services, started.case, f"{type(error).__name__}: {error}")


async def _fail(services: Bookkeeping, case: Case, error: str) -> Failed:
    ops = plan_analysis_failure(case=case, error=error, now=services.clock.now())
    await services.ledger.commit(ops)
    return Failed(case=case, error=error)


async def _analyze(
    services: Bookkeeping, investigator: Investigator, narrator: Narrator, *, case: Case
) -> Analyzed | Skipped:
    ledger = services.ledger
    brief = build_brief(case)
    investigation = await investigator.investigate(brief)
    reported = grounded(usable(investigation.signals), brief.text)
    entities = brief.entities + investigation.entities
    signals = usable(
        await official_signals(services, entities)
        + await recidivism_signals(services, entities, case_id=case.id)
        + reported
    )
    analyzable = bool(normalize_text(brief.text)) or bool(brief.links)
    assessment = assess(signals, missing=investigation.missing, analyzable=analyzable)
    summary = await narrator.narrate(
        VerdictBrief(
            case_id=case.id,
            language=brief.language,
            level=assessment.level,
            outcome=assessment.outcome,
            actions=assessment.actions,
            missing=assessment.missing,
            signals=signals,
        )
    )
    linked = frozenset(link.entity_id for link in await ledger.entities_of_case(case.id))
    plan = plan_analysis_completion(
        case=case,
        draft=VerdictDraft(
            assessment=assessment,
            signals=signals,
            entities=investigation.entities,
            language=brief.language,
            summary=summary,
        ),
        known=await known_entities(services, investigation.entities),
        linked=linked,
        previous=await ledger.current_verdict(case.id),
        signal_ids=tuple(services.ids.new_id() for _ in signals),
        now=services.clock.now(),
    )
    if isinstance(plan, Obsolete):
        return Skipped(plan.reason)
    try:
        await ledger.commit(plan.ops)
    except LedgerConflictError:
        return Skipped("case changed underneath")
    return Analyzed(case=plan.case, verdict=plan.verdict)
