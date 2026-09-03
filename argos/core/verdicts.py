"""Plan durable del análisis de un caso: arranque, memoria compartida y veredicto."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from argos.core.analysis import Assessment, DraftEntity, DraftSignal
from argos.core.ledger import Obsolete, command_entry, event_entry, is_current_attempt, new_job
from argos.core.messages import success_subject
from argos.core.model import (
    TERMINAL_CASE_STATES,
    Attempt,
    AttemptState,
    Case,
    CaseEntity,
    CaseState,
    Entity,
    Insert,
    Job,
    JobState,
    JobType,
    LedgerOp,
    Signal,
    Update,
    Verdict,
    VerdictOutcome,
    VerdictState,
    analysis_job_id,
    case_entity_id,
    entity_id,
    verdict_id,
)
from argos.core.policy import JobPolicy

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
    job: Job
    case: Case
    verdict: Verdict


def plan_analysis_start(
    *, case: Case, job: Job, attempt: Attempt, now: datetime
) -> AnalysisStarted | Obsolete:
    if job.type is not JobType.CASE_ANALYZE:
        return Obsolete("job is not an analysis")
    if not is_current_attempt(job, attempt):
        return Obsolete("attempt is not the running one")
    if case.id != job.case_id or case.tenant_id != job.tenant_id:
        return Obsolete("case does not belong to the job")
    if case.state in TERMINAL_CASE_STATES:
        return Obsolete(f"case is {case.state}")
    if case.state is CaseState.ANALYZING:
        return AnalysisStarted(ops=(), case=case)
    analyzing = replace(case, state=CaseState.ANALYZING, updated_at=now, revision=case.revision + 1)
    return AnalysisStarted(ops=(Update(analyzing),), case=analyzing)


def _memory_ops(
    *,
    case: Case,
    entities: Sequence[DraftEntity],
    known: Mapping[str, Entity],
    linked: frozenset[str],
    now: datetime,
) -> tuple[LedgerOp, ...]:
    ops: list[LedgerOp] = []
    seen: set[str] = set()
    for drafted in entities:
        identifier = entity_id(drafted.kind, drafted.value)
        if identifier in seen:
            continue
        seen.add(identifier)
        stored = known.get(identifier)
        if stored is None:
            ops.append(
                Insert(
                    Entity(
                        id=identifier,
                        kind=drafted.kind,
                        value=drafted.value,
                        strength=drafted.strength,
                        first_seen_at=now,
                        last_seen_at=now,
                        revision=0,
                    )
                )
            )
        else:
            ops.append(Update(replace(stored, last_seen_at=now, revision=stored.revision + 1)))
        if identifier not in linked:
            ops.append(
                Insert(
                    CaseEntity(
                        id=case_entity_id(case.id, identifier),
                        tenant_id=case.tenant_id,
                        case_id=case.id,
                        entity_id=identifier,
                        created_at=now,
                        revision=0,
                    )
                )
            )
    return tuple(ops)


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
                    tenant_id=case.tenant_id,
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
    job: Job,
    attempt: Attempt,
    draft: VerdictDraft,
    known: Mapping[str, Entity],
    linked: frozenset[str],
    previous: Verdict | None,
    signal_ids: Sequence[str],
    now: datetime,
) -> AnalysisClosed | Obsolete:
    if not is_current_attempt(job, attempt):
        return Obsolete("attempt is not the running one")
    if case.id != job.case_id or case.tenant_id != job.tenant_id:
        return Obsolete("case does not belong to the job")
    version = previous.version + 1 if previous is not None else 1
    verdict = Verdict(
        id=verdict_id(case.id, version),
        tenant_id=case.tenant_id,
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
    closed = replace(case, state=CASE_STATE_OF_OUTCOME[draft.assessment.outcome], updated_at=now)
    closed = replace(closed, revision=case.revision + 1)
    succeeded = replace(
        attempt, state=AttemptState.SUCCEEDED, finished_at=now, revision=attempt.revision + 1
    )
    completed = replace(
        job, state=JobState.COMPLETED, lease_until=None, updated_at=now, revision=job.revision + 1
    )
    ops: list[LedgerOp] = list(
        _memory_ops(case=case, entities=draft.entities, known=known, linked=linked, now=now)
    )
    ops.extend(_signal_ops(case=case, signals=draft.signals, ids=signal_ids, now=now))
    if previous is not None:
        ops.append(
            Update(replace(previous, state=VerdictState.SUPERSEDED, revision=previous.revision + 1))
        )
    ops.extend(
        (
            Insert(verdict),
            Update(closed),
            Update(succeeded),
            Update(completed),
            Insert(event_entry(completed, subject=success_subject(job.type), now=now)),
        )
    )
    return AnalysisClosed(ops=tuple(ops), job=completed, case=closed, verdict=verdict)


@dataclass(frozen=True)
class PlannedAnalysis:
    ops: tuple[LedgerOp, ...]
    job_id: str


@dataclass(frozen=True)
class AnalysisNotDue:
    reason: str


def plan_case_analysis(
    *,
    case: Case,
    jobs: Sequence[Job],
    now: datetime,
    policy: JobPolicy,
    correlation_id: str,
) -> PlannedAnalysis | AnalysisNotDue:
    """El resumer solo crea el trabajo cuando el caso no tiene documentos pendientes."""
    if case.state in TERMINAL_CASE_STATES:
        return AnalysisNotDue(f"case is {case.state}")
    if case.state is CaseState.ANALYZING:
        return AnalysisNotDue("case is already being analyzed")
    if any(
        job.type is JobType.DOCUMENT_EXTRACT and job.state in (JobState.QUEUED, JobState.RUNNING)
        for job in jobs
    ):
        return AnalysisNotDue("documents are still pending")
    analyses = [job for job in jobs if job.type is JobType.CASE_ANALYZE]
    if any(job.state in (JobState.QUEUED, JobState.RUNNING) for job in analyses):
        return AnalysisNotDue("analysis is already queued")
    job = new_job(
        job_id=analysis_job_id(case.id, len(analyses) + 1),
        tenant_id=case.tenant_id,
        case_id=case.id,
        job_type=JobType.CASE_ANALYZE,
        document_id=None,
        now=now,
        policy=policy,
        extractor_version="",
        options="{}",
        correlation_id=correlation_id,
    )
    return PlannedAnalysis(
        ops=(Insert(job), Insert(command_entry(job, attempt=1, now=now, not_before=now))),
        job_id=job.id,
    )
