"""Proceso case analyzer: reclama el trabajo `case.analyze` y ejecuta el workflow."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass

from argos.agents.cluster import AgentCluster, build_cluster
from argos.config import Settings
from argos.core.messages import CASE_ANALYZER
from argos.core.model import FailureKind
from argos.core.policy import Policy
from argos.core.ports import Delivery, Investigator, MessageSource, Narrator
from argos.platform.agno_db import build_agno_db
from argos.platform.bus import JetStreamBus
from argos.platform.clock import SystemClock
from argos.platform.ids import TimeOrderedIds
from argos.platform.ledger import ledger_for
from argos.services.runtime import Sleep, Stop, stop_on_signals
from argos.usecases.analysis import analyze_case
from argos.usecases.consumers import ClaimedAttempt, Skipped, claim_attempt, fail_attempt
from argos.usecases.deps import Analyzing, Bookkeeping

UNEXPECTED = "case.analysis_failed"

Cluster = Callable[[str, str], AgentCluster]


@dataclass(frozen=True)
class AnalyzerTick:
    analyzed: tuple[str, ...]
    skipped: tuple[str, ...]


async def handle(
    services: Bookkeeping, cluster: Cluster, delivery: Delivery, *, consumer: str
) -> str | None:
    claimed = await claim_attempt(services, delivery.message, consumer=consumer)
    if isinstance(claimed, Skipped):
        return None
    await _analyze(services, cluster, claimed)
    return claimed.job.id


async def _analyze(services: Bookkeeping, cluster: Cluster, claimed: ClaimedAttempt) -> None:
    agents = cluster(claimed.job.tenant_id, claimed.job.case_id)
    try:
        await run_analysis(services, agents.investigator, agents.narrator, claimed=claimed)
    finally:
        await agents.close()


async def run_analysis(
    services: Bookkeeping,
    investigator: Investigator,
    narrator: Narrator,
    *,
    claimed: ClaimedAttempt,
) -> None:
    try:
        await analyze_case(
            services, investigator, narrator, job=claimed.job, attempt=claimed.attempt
        )
    except Exception:
        await fail_attempt(
            services,
            job_id=claimed.job.id,
            attempt_number=claimed.attempt.number,
            kind=FailureKind.TRANSIENT,
            code=UNEXPECTED,
        )
        raise


async def analyzer_tick(
    services: Bookkeeping,
    cluster: Cluster,
    source: MessageSource,
    *,
    consumer: str,
    batch: int = 4,
    timeout: float = 1.0,
) -> AnalyzerTick:
    analyzed: list[str] = []
    skipped: list[str] = []
    for delivery in await source.fetch(limit=batch, timeout=timeout):
        handled = await handle(services, cluster, delivery, consumer=consumer)
        if handled is None:
            skipped.append(delivery.message.job_id)
        else:
            analyzed.append(handled)
        await delivery.ack()
    return AnalyzerTick(analyzed=tuple(analyzed), skipped=tuple(skipped))


async def run_analyzer(
    services: Bookkeeping,
    cluster: Cluster,
    source: MessageSource,
    *,
    consumer: str,
    stop: Stop,
    sleep: Sleep,
    interval: float,
) -> list[AnalyzerTick]:
    ticks: list[AnalyzerTick] = []
    while not stop():
        ticks.append(await analyzer_tick(services, cluster, source, consumer=consumer))
        if stop():
            break
        await sleep(interval)
    return ticks


async def serve(settings: Settings, policy: Policy, *, interval: float) -> None:
    ledger = ledger_for(settings, "analyzer")
    bus = JetStreamBus(settings.nats_url, policy=policy.jobs)
    await ledger.connect()
    await bus.connect()
    services = Analyzing(ledger=ledger, clock=SystemClock(), ids=TimeOrderedIds(), policy=policy)
    sessions = build_agno_db(settings)

    def cluster(tenant_id: str, case_id: str) -> AgentCluster:
        return build_cluster(services, settings, tenant_id=tenant_id, case_id=case_id, db=sessions)

    try:
        await run_analyzer(
            services,
            cluster,
            await bus.deliveries(CASE_ANALYZER),
            consumer=CASE_ANALYZER.durable,
            stop=stop_on_signals(),
            sleep=asyncio.sleep,
            interval=interval,
        )
    finally:
        await bus.close()
        await ledger.close()


def main() -> None:
    asyncio.run(serve(Settings(), Policy(), interval=1.0))
    sys.stdout.write("analyzer stopped\n")
