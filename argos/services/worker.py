"""Proceso worker: reclama comandos de extracción, los ejecuta y confirma su entrega."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from argos.config import Settings
from argos.core.messages import DOCUMENT_EXTRACTOR
from argos.core.model import FailureKind
from argos.core.policy import Policy
from argos.core.ports import Delivery, MessageSource
from argos.devtools.bootstrap_store import build_store
from argos.platform.bus import JetStreamBus
from argos.platform.clock import SystemClock
from argos.platform.ids import TimeOrderedIds
from argos.platform.ledger import SurrealLedger
from argos.platform.ocr import TesseractOcr
from argos.platform.pdf import PdfiumReader
from argos.services.runtime import Sleep, Stop, stop_on_signals
from argos.usecases.consumers import ClaimedAttempt, Skipped, claim_attempt, fail_attempt
from argos.usecases.deps import Services
from argos.usecases.extract import PdfTools, extract_document

UNEXPECTED = "worker.unexpected"


@dataclass(frozen=True)
class WorkerTick:
    extracted: tuple[str, ...]
    skipped: tuple[str, ...]


async def handle(
    services: Services, tools: PdfTools, delivery: Delivery, *, consumer: str
) -> str | None:
    claimed = await claim_attempt(services, delivery.message, consumer=consumer)
    if isinstance(claimed, Skipped):
        return None
    await _extract(services, tools, claimed)
    return claimed.job.id


async def _extract(services: Services, tools: PdfTools, claimed: ClaimedAttempt) -> None:
    try:
        await extract_document(services, tools, job=claimed.job, attempt=claimed.attempt)
    except Exception:
        await fail_attempt(
            services,
            job_id=claimed.job.id,
            attempt_number=claimed.attempt.number,
            kind=FailureKind.TRANSIENT,
            code=UNEXPECTED,
        )
        raise


async def worker_tick(
    services: Services,
    tools: PdfTools,
    source: MessageSource,
    *,
    consumer: str,
    batch: int = 8,
    timeout: float = 1.0,
) -> WorkerTick:
    extracted: list[str] = []
    skipped: list[str] = []
    for delivery in await source.fetch(limit=batch, timeout=timeout):
        handled = await handle(services, tools, delivery, consumer=consumer)
        if handled is None:
            skipped.append(delivery.message.job_id)
        else:
            extracted.append(handled)
        await delivery.ack()
    return WorkerTick(extracted=tuple(extracted), skipped=tuple(skipped))


async def run_worker(
    services: Services,
    tools: PdfTools,
    source: MessageSource,
    *,
    consumer: str,
    stop: Stop,
    sleep: Sleep,
    interval: float,
) -> list[WorkerTick]:
    ticks: list[WorkerTick] = []
    while not stop():
        ticks.append(await worker_tick(services, tools, source, consumer=consumer))
        if stop():
            break
        await sleep(interval)
    return ticks


async def serve(settings: Settings, policy: Policy, *, interval: float) -> None:
    ledger = SurrealLedger(
        url=f"{settings.surreal_ws_url}/rpc",
        namespace=settings.ops_namespace,
        database=settings.ops_database,
        user=settings.surreal_ledger_user,
        password=settings.surreal_ledger_password.get_secret_value(),
    )
    bus = JetStreamBus(settings.nats_url, policy=policy.jobs)
    store = build_store(settings)
    await ledger.connect()
    await bus.connect()
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
    tools = PdfTools(reader=PdfiumReader(), ocr=TesseractOcr())
    try:
        await run_worker(
            services,
            tools,
            await bus.deliveries(DOCUMENT_EXTRACTOR),
            consumer=DOCUMENT_EXTRACTOR.durable,
            stop=stop_on_signals(),
            sleep=asyncio.sleep,
            interval=interval,
        )
    finally:
        await store.close()
        await bus.close()
        await ledger.close()


def main() -> None:
    asyncio.run(serve(Settings(), Policy(), interval=1.0))
    sys.stdout.write("worker stopped\n")
