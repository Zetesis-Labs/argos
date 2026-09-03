"""Outbox dispatcher (S02 §8): publica lo confirmado y recupera arrendamientos vencidos."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.ledger import (
    Obsolete,
    plan_lease_recovery,
    plan_outbox_claim,
    plan_outbox_published,
    plan_outbox_release,
)
from argos.core.messages import MESSAGE_ID_HEADER, JobMessage, encode_job_message
from argos.core.model import JobState, OutboxEntry, Update
from argos.core.ports import BusUnavailableError, LedgerConflictError, OutboundMessage
from argos.usecases.deps import Dispatching


@dataclass(frozen=True)
class DispatchReport:
    published: tuple[str, ...]
    failed: tuple[str, ...]
    skipped: tuple[str, ...]


def outbound_message(entry: OutboxEntry) -> OutboundMessage:
    return OutboundMessage(
        subject=entry.subject,
        message_id=entry.message_id,
        payload=encode_job_message(JobMessage(job_id=entry.job_id, attempt=entry.attempt)),
        headers={MESSAGE_ID_HEADER: entry.message_id},
    )


async def dispatch_once(services: Dispatching, *, limit: int = 50) -> DispatchReport:
    ledger = services.ledger
    published: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    for entry in await ledger.pending_outbox(services.clock.now(), limit=limit):
        claimed = plan_outbox_claim(entry, now=services.clock.now(), policy=services.policy.jobs)
        try:
            await ledger.commit([Update(claimed)])
        except LedgerConflictError:
            skipped.append(entry.id)
            continue
        try:
            await services.bus.publish(outbound_message(claimed))
        except BusUnavailableError:
            await ledger.commit([Update(plan_outbox_release(claimed))])
            failed.append(entry.id)
            continue
        await ledger.commit([Update(plan_outbox_published(claimed, now=services.clock.now()))])
        published.append(entry.id)
    return DispatchReport(published=tuple(published), failed=tuple(failed), skipped=tuple(skipped))


@dataclass(frozen=True)
class RecoveryReport:
    requeued: tuple[str, ...]
    failed: tuple[str, ...]
    skipped: tuple[str, ...]


async def recover_leases_once(services: Dispatching) -> RecoveryReport:
    ledger = services.ledger
    now = services.clock.now()
    requeued: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    for job in await ledger.jobs_with_expired_lease(now):
        current = [
            attempt for attempt in await ledger.attempts(job.id) if attempt.number == job.attempt
        ]
        if not current:
            skipped.append(job.id)
            continue
        plan = plan_lease_recovery(
            job=job, attempt=current[0], now=now, policy=services.policy.jobs
        )
        if isinstance(plan, Obsolete):
            skipped.append(job.id)
            continue
        try:
            await ledger.commit(plan.ops)
        except LedgerConflictError:
            skipped.append(job.id)
            continue
        if plan.job.state is JobState.FAILED:
            failed.append(job.id)
        else:
            requeued.append(job.id)
    return RecoveryReport(requeued=tuple(requeued), failed=tuple(failed), skipped=tuple(skipped))
