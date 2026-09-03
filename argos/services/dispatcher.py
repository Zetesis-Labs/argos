"""Proceso dispatcher: publica el outbox confirmado y reencola arrendamientos vencidos."""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from argos.config import Settings
from argos.core.policy import Policy
from argos.platform.bus import JetStreamBus
from argos.platform.clock import SystemClock
from argos.platform.ledger import SurrealLedger
from argos.usecases.deps import Dispatching
from argos.usecases.dispatch import (
    DispatchReport,
    RecoveryReport,
    dispatch_once,
    recover_leases_once,
)

Stop = Callable[[], bool]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Tick:
    dispatch: DispatchReport
    recovery: RecoveryReport


async def tick(services: Dispatching) -> Tick:
    return Tick(
        dispatch=await dispatch_once(services),
        recovery=await recover_leases_once(services),
    )


async def run_dispatcher(
    services: Dispatching, *, stop: Stop, sleep: Sleep, interval: float
) -> list[Tick]:
    ticks: list[Tick] = []
    while not stop():
        ticks.append(await tick(services))
        if stop():
            break
        await sleep(interval)
    return ticks


def _stop_on_signals() -> Stop:
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(received, stopping.set)
    return stopping.is_set


async def serve(settings: Settings, policy: Policy, *, interval: float) -> None:
    ledger = SurrealLedger(
        url=f"{settings.surreal_ws_url}/rpc",
        namespace=settings.ops_namespace,
        database=settings.ops_database,
        user=settings.surreal_ledger_user,
        password=settings.surreal_ledger_password.get_secret_value(),
    )
    bus = JetStreamBus(settings.nats_url, policy=policy.jobs)
    await ledger.connect()
    await bus.connect()
    services = Dispatching(ledger=ledger, bus=bus, clock=SystemClock(), policy=policy)
    try:
        await run_dispatcher(
            services, stop=_stop_on_signals(), sleep=asyncio.sleep, interval=interval
        )
    finally:
        await bus.close()
        await ledger.close()


def main() -> None:
    asyncio.run(serve(Settings(), Policy(), interval=1.0))
    sys.stdout.write("dispatcher stopped\n")
