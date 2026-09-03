"""Workflow de veredicto: mueve el caso, coordina el clúster y cierra con un
veredicto durable. Las transiciones y el nivel son código, nunca un prompt."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.analysis import assess, usable, verdict_language
from argos.core.ledger import Obsolete
from argos.core.model import (
    Attempt,
    Case,
    Entity,
    ExtractionState,
    Job,
    JobState,
    JobType,
    Verdict,
    entity_id,
)
from argos.core.ports import (
    CaseBrief,
    ExtractionRef,
    Investigator,
    LedgerConflictError,
    Narrator,
    VerdictBrief,
)
from argos.core.verdicts import VerdictDraft, plan_analysis_completion, plan_analysis_start
from argos.usecases.consumers import Skipped
from argos.usecases.deps import Bookkeeping


@dataclass(frozen=True)
class Analyzed:
    case: Case
    verdict: Verdict


def missing_document(document_id: str | None) -> str:
    return f"document:{document_id}" if document_id else "document"


async def build_brief(services: Bookkeeping, case: Case) -> CaseBrief:
    ledger = services.ledger
    extractions = [
        extraction
        for extraction in await ledger.extractions_of_case(case.id)
        if extraction.state is ExtractionState.AVAILABLE
    ]
    failed = [
        job
        for job in await ledger.jobs_of_case(case.id)
        if job.type is JobType.DOCUMENT_EXTRACT and job.state is JobState.FAILED
    ]
    return CaseBrief(
        tenant_id=case.tenant_id,
        case_id=case.id,
        language=verdict_language(case.language),
        correlation_id=case.correlation_id,
        extractions=tuple(
            ExtractionRef(
                extraction_id=extraction.id,
                document_id=extraction.document_id,
                page_count=extraction.page_count,
            )
            for extraction in extractions
        ),
        missing=tuple(missing_document(job.document_id) for job in failed),
    )


async def analyze_case(
    services: Bookkeeping,
    investigator: Investigator,
    narrator: Narrator,
    *,
    job: Job,
    attempt: Attempt,
) -> Analyzed | Skipped:
    ledger = services.ledger
    case = await ledger.case(job.case_id)
    if case is None:
        return Skipped("unknown case")
    started = plan_analysis_start(case=case, job=job, attempt=attempt, now=services.clock.now())
    if isinstance(started, Obsolete):
        return Skipped(started.reason)
    try:
        await ledger.commit(started.ops)
    except LedgerConflictError:
        return Skipped("case changed underneath")
    analyzing = started.case

    brief = await build_brief(services, analyzing)
    investigation = await investigator.investigate(brief)
    missing = tuple(dict.fromkeys(brief.missing + investigation.missing))
    signals = usable(investigation.signals)
    analyzable = bool(brief.extractions) or analyzing.notice_hash is not None
    assessment = assess(signals, missing=missing, analyzable=analyzable)
    summary = await narrator.narrate(
        VerdictBrief(
            case_id=analyzing.id,
            language=brief.language,
            level=assessment.level,
            outcome=assessment.outcome,
            actions=assessment.actions,
            missing=assessment.missing,
            signals=signals,
        )
    )

    known: dict[str, Entity] = {}
    for drafted in investigation.entities:
        stored = await ledger.entity_by_value(drafted.kind, drafted.value)
        if stored is not None:
            known[entity_id(drafted.kind, drafted.value)] = stored
    linked = frozenset(link.entity_id for link in await ledger.entities_of_case(analyzing.id))
    plan = plan_analysis_completion(
        case=analyzing,
        job=job,
        attempt=attempt,
        draft=VerdictDraft(
            assessment=assessment,
            signals=signals,
            entities=investigation.entities,
            language=brief.language,
            summary=summary,
        ),
        known=known,
        linked=linked,
        previous=await ledger.current_verdict(analyzing.id),
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
