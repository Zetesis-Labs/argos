"""Proceso gateway: el único de larga vida que tiene Argos."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from agno.models.openai import OpenAIChat
from fastapi import FastAPI

from argos.agents.cluster import build_advisor, build_cluster
from argos.api.gateway import Gateway, build_app
from argos.config import Settings
from argos.core.identity import parse_registry
from argos.core.policy import Policy
from argos.core.ports import CaseAdvisor
from argos.platform.agno_db import build_agno_db
from argos.platform.clock import SystemClock
from argos.platform.ids import TimeOrderedIds
from argos.platform.ledger import SurrealLedger, ledger_for
from argos.platform.llm import build_model, close_model
from argos.usecases.deps import Services
from argos.usecases.gateway import Analysts

VERSION = "0.3.0"


@dataclass(frozen=True)
class Wiring:
    gateway: Gateway
    ledger: SurrealLedger
    model: OpenAIChat


def build_gateway(settings: Settings, policy: Policy) -> Wiring:
    ledger = ledger_for(settings, "gateway")
    services = Services(
        ledger=ledger, clock=SystemClock(), ids=TimeOrderedIds(), policy=policy
    )
    sessions = build_agno_db(settings)
    model = build_model(settings, settings.analysis_model)

    def investigators(tenant_id: str, case_id: str) -> Analysts:
        cluster = build_cluster(
            services, settings, tenant_id=tenant_id, case_id=case_id, db=sessions
        )
        return cluster.investigator, cluster.narrator

    def advisors(tenant_id: str, case_id: str) -> CaseAdvisor:
        return build_advisor(
            model, services=services, tenant_id=tenant_id, case_id=case_id, db=sessions
        )

    gateway = Gateway(
        services=services,
        investigators=investigators,
        advisors=advisors,
        identities=dict(parse_registry(settings.gateway_identities)),
        version=VERSION,
    )
    return Wiring(gateway=gateway, ledger=ledger, model=model)


@asynccontextmanager
async def connected(wiring: Wiring) -> AsyncGenerator[None]:
    await wiring.ledger.connect()
    try:
        yield
    finally:
        await wiring.ledger.close()
        await close_model(wiring.model)


def main() -> None:
    settings, policy = Settings(), Policy()
    wiring = build_gateway(settings, policy)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with connected(wiring):
            yield

    app = build_app(wiring.gateway, lifespan=lifespan)
    # Dentro del contenedor hay que escuchar en todas sus interfaces o el reenvío
    # de Docker no llega; el compose ya publica el puerto solo en el loopback del host.
    uvicorn.run(app, host="0.0.0.0", port=settings.gateway_port)  # noqa: S104
