"""Proceso janitor: barre el staging caducado y aplica la retención."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from argos.config import Settings
from argos.core.policy import Policy
from argos.devtools.bootstrap_store import build_store
from argos.platform.bus import JetStreamBus
from argos.platform.clock import SystemClock
from argos.platform.ids import TimeOrderedIds
from argos.platform.ledger import ledger_for
from argos.services.runtime import Sleep, Stop, stop_on_signals
from argos.usecases.deps import Services
from argos.usecases.janitor import SweepReport, enforce_retention, sweep_staging


@dataclass(frozen=True)
class JanitorTick:
    staging: SweepReport
    retention: SweepReport


async def tick(services: Services) -> JanitorTick:
    return JanitorTick(
        staging=await sweep_staging(services),
        retention=await enforce_retention(services),
    )


async def run_janitor(
    services: Services, *, stop: Stop, sleep: Sleep, interval: float
) -> list[JanitorTick]:
    ticks: list[JanitorTick] = []
    while not stop():
        ticks.append(await tick(services))
        if stop():
            break
        await sleep(interval)
    return ticks


async def serve(settings: Settings, policy: Policy, *, interval: float) -> None:
    ledger = ledger_for(settings, "janitor")
    bus = JetStreamBus(settings.nats_url, policy=policy.jobs)
    store = build_store(settings)
    await ledger.connect()
    await store.connect()
    services = Services(
        ledger=ledger,
        object_store=store,
        bus=bus,
        clock=SystemClock(),
        ids=TimeOrderedIds(),
        policy=policy,
        bucket=settings.artifact_bucket,
    )
    try:
        await run_janitor(services, stop=stop_on_signals(), sleep=asyncio.sleep, interval=interval)
    finally:
        await store.close()
        await ledger.close()


def main() -> None:
    asyncio.run(serve(Settings(), Policy(), interval=60.0))
    sys.stdout.write("janitor stopped\n")
