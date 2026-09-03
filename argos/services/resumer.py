"""Proceso resumer: consume eventos de documento y crea el trabajo de análisis."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from argos.config import Settings
from argos.core.messages import WORKFLOW_RESUMER
from argos.core.policy import Policy
from argos.core.ports import Delivery, MessageSource
from argos.platform.bus import JetStreamBus
from argos.platform.clock import SystemClock
from argos.platform.ids import TimeOrderedIds
from argos.platform.ledger import ledger_for
from argos.services.runtime import Sleep, Stop, stop_on_signals
from argos.usecases.consumers import Skipped
from argos.usecases.deps import Analyzing, Bookkeeping
from argos.usecases.resume import resume_case


@dataclass(frozen=True)
class ResumerTick:
    queued: tuple[str, ...]
    skipped: tuple[str, ...]


async def handle(services: Bookkeeping, delivery: Delivery) -> str | None:
    resumed = await resume_case(services, delivery.message)
    return None if isinstance(resumed, Skipped) else resumed.job_id


async def resumer_tick(
    services: Bookkeeping, source: MessageSource, *, batch: int = 16, timeout: float = 1.0
) -> ResumerTick:
    queued: list[str] = []
    skipped: list[str] = []
    for delivery in await source.fetch(limit=batch, timeout=timeout):
        handled = await handle(services, delivery)
        if handled is None:
            skipped.append(delivery.message.job_id)
        else:
            queued.append(handled)
        await delivery.ack()
    return ResumerTick(queued=tuple(queued), skipped=tuple(skipped))


async def run_resumer(
    services: Bookkeeping, source: MessageSource, *, stop: Stop, sleep: Sleep, interval: float
) -> list[ResumerTick]:
    ticks: list[ResumerTick] = []
    while not stop():
        ticks.append(await resumer_tick(services, source))
        if stop():
            break
        await sleep(interval)
    return ticks


async def serve(settings: Settings, policy: Policy, *, interval: float) -> None:
    ledger = ledger_for(settings, "resumer")
    bus = JetStreamBus(settings.nats_url, policy=policy.jobs)
    await ledger.connect()
    await bus.connect()
    services = Analyzing(ledger=ledger, clock=SystemClock(), ids=TimeOrderedIds(), policy=policy)
    try:
        await run_resumer(
            services,
            await bus.deliveries(WORKFLOW_RESUMER),
            stop=stop_on_signals(),
            sleep=asyncio.sleep,
            interval=interval,
        )
    finally:
        await bus.close()
        await ledger.close()


def main() -> None:
    asyncio.run(serve(Settings(), Policy(), interval=1.0))
    sys.stdout.write("resumer stopped\n")
