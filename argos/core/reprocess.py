"""Reproceso del curador (S02 §11): trabajo nuevo vinculado, nunca borrar historia."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from argos.core.ledger import command_entry, new_job
from argos.core.model import (
    Case,
    CaseState,
    Document,
    DocumentState,
    Insert,
    Job,
    JobState,
    JobType,
    LedgerOp,
    Update,
)
from argos.core.policy import JobPolicy

ACTIVE_JOB_STATES = (JobState.QUEUED, JobState.RUNNING)


@dataclass(frozen=True)
class PlannedReprocess:
    ops: tuple[LedgerOp, ...]
    job_id: str
    options: str


@dataclass(frozen=True)
class ReprocessRefused:
    code: str


def reprocess_options(revision: int) -> str:
    return json.dumps({"revision": revision}, sort_keys=True)


def plan_reprocess(
    *,
    case: Case,
    document: Document,
    jobs: Sequence[Job],
    job_id: str,
    now: datetime,
    policy: JobPolicy,
    extractor_version: str,
    correlation_id: str,
) -> PlannedReprocess | ReprocessRefused:
    if document.case_id != case.id or document.tenant_id != case.tenant_id:
        return ReprocessRefused("document.not_found")
    if case.state is CaseState.ANALYZING:
        return ReprocessRefused("case.busy")
    extractions = [job for job in jobs if job.type is JobType.DOCUMENT_EXTRACT]
    mine = [job for job in extractions if job.document_id == document.id]
    if not mine:
        return ReprocessRefused("document.never_processed")
    if any(job.state in ACTIVE_JOB_STATES for job in mine):
        return ReprocessRefused("job.busy")
    previous = mine[-1]
    job = new_job(
        job_id=job_id,
        tenant_id=case.tenant_id,
        case_id=case.id,
        job_type=JobType.DOCUMENT_EXTRACT,
        document_id=document.id,
        now=now,
        policy=policy,
        extractor_version=extractor_version,
        options=reprocess_options(len(mine) + 1),
        correlation_id=correlation_id,
        previous_job_id=previous.id,
    )
    ops: list[LedgerOp] = []
    if case.state is not CaseState.AWAITING_PROCESSING:
        ops.append(
            Update(
                replace(
                    case,
                    state=CaseState.AWAITING_PROCESSING,
                    updated_at=now,
                    revision=case.revision + 1,
                )
            )
        )
    if document.state is not DocumentState.ACCEPTED:
        ops.append(
            Update(replace(document, state=DocumentState.ACCEPTED, revision=document.revision + 1))
        )
    ops.append(Insert(job))
    ops.append(Insert(command_entry(job, attempt=1, now=now, not_before=now)))
    return PlannedReprocess(ops=tuple(ops), job_id=job.id, options=job.options)
