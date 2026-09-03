"""Métricas del libro (S02 §13). Solo recuentos y tiempos: ni casos ni contenido."""

from __future__ import annotations

from argos.core.observability import AWAITING_STATES, Metrics, summarize
from argos.usecases.deps import Bookkeeping

OUTBOX_WINDOW = 500


async def collect_metrics(services: Bookkeeping) -> Metrics:
    now = services.clock.now()
    oldest = await services.ledger.oldest_queued_job()
    return summarize(
        counts=await services.ledger.job_counts(),
        oldest_queued_at=None if oldest is None else oldest.created_at,
        outbox=await services.ledger.pending_outbox(now, limit=OUTBOX_WINDOW),
        awaiting=await services.ledger.count_cases(AWAITING_STATES),
        now=now,
    )
