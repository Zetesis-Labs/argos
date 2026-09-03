"""Proceso gateway: sirve las capacidades sobre AgentOS."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

import uvicorn
from agno.models.openai import OpenAIChat
from agno.os import AgentOS
from fastapi import FastAPI

from argos.agents.cluster import build_advisor
from argos.api.gateway import Gateway, build_app
from argos.config import Settings
from argos.core.identity import parse_registry
from argos.core.policy import Policy
from argos.core.ports import CaseAdvisor
from argos.devtools.bootstrap_store import build_store
from argos.platform.agno_db import build_agno_db
from argos.platform.bus import JetStreamBus
from argos.platform.clock import SystemClock
from argos.platform.ids import TimeOrderedIds
from argos.platform.ledger import SurrealLedger, ledger_for
from argos.platform.llm import build_model, close_model
from argos.platform.objects import RustFsObjectStore
from argos.usecases.deps import Services

VERSION = "0.2.0"

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


@dataclass(frozen=True)
class Wiring:
    gateway: Gateway
    ledger: SurrealLedger
    bus: JetStreamBus
    store: RustFsObjectStore
    model: OpenAIChat


def build_gateway(settings: Settings, policy: Policy) -> Wiring:
    ledger = ledger_for(settings, "gateway")
    bus = JetStreamBus(settings.nats_url, policy=policy.jobs)
    store = build_store(settings)
    services = Services(
        ledger=ledger,
        object_store=store,
        bus=bus,
        clock=SystemClock(),
        ids=TimeOrderedIds(),
        policy=policy,
        bucket=settings.artifact_bucket,
    )
    sessions = build_agno_db(settings)
    model = build_model(settings, settings.analysis_model)

    def advisors(tenant_id: str, case_id: str) -> CaseAdvisor:
        return build_advisor(
            model, services=services, tenant_id=tenant_id, case_id=case_id, db=sessions
        )

    gateway = Gateway(
        services=services,
        advisors=advisors,
        identities=dict(parse_registry(settings.gateway_identities)),
        version=VERSION,
        public_url=settings.gateway_public_url,
    )
    return Wiring(gateway=gateway, ledger=ledger, bus=bus, store=store, model=model)


def serve_app(gateway: Gateway, settings: Settings, *, lifespan: Lifespan | None = None) -> FastAPI:
    """AgentOS aloja el gateway; no se le registra ningún agente, así que su plano
    de control no descubre especialistas ni workers (constitución §8)."""
    agent_os = AgentOS(
        id="argos",
        name="Argos",
        version=gateway.version,
        base_app=build_app(gateway),
        db=build_agno_db(settings),
        lifespan=lifespan,
        telemetry=False,
    )
    return agent_os.get_app()


@asynccontextmanager
async def connected(wiring: Wiring) -> AsyncGenerator[None]:
    await wiring.ledger.connect()
    await wiring.bus.connect()
    await wiring.store.connect()
    try:
        yield
    finally:
        await wiring.store.close()
        await wiring.bus.close()
        await wiring.ledger.close()
        await close_model(wiring.model)


def main() -> None:
    settings, policy = Settings(), Policy()
    wiring = build_gateway(settings, policy)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with connected(wiring):
            yield

    app = serve_app(wiring.gateway, settings, lifespan=lifespan)
    uvicorn.run(app, host="127.0.0.1", port=settings.gateway_port)
