"""Proceso del API local."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn
from agno.models.openai import OpenAIChat
from fastapi import FastAPI

from argos.api.gateway import Gateway, build_app
from argos.config import Settings
from argos.core.policy import Policy
from argos.platform.ledger import SurrealLedger
from argos.platform.llm import close_model
from argos.wiring import Wiring, build_wiring

VERSION = "0.4.0"


@dataclass(frozen=True)
class Served:
    gateway: Gateway
    ledger: SurrealLedger
    model: OpenAIChat


def build_gateway(wiring: Wiring) -> Gateway:
    return Gateway(
        services=wiring.services,
        investigators=wiring.investigators,
        advisors=wiring.advisors,
        version=VERSION,
    )


@asynccontextmanager
async def connected(wiring: Wiring) -> AsyncGenerator[None]:
    await wiring.ledger.connect()
    try:
        yield
    finally:
        await wiring.ledger.close()
        await close_model(wiring.model)


def main() -> None:
    wiring = build_wiring(Settings(), Policy())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with connected(wiring):
            yield

    app = build_app(build_gateway(wiring), lifespan=lifespan)
    # Dentro del contenedor hay que escuchar en todas sus interfaces o el reenvío
    # de Docker no llega; el compose ya publica el puerto solo en el loopback del host.
    uvicorn.run(app, host="0.0.0.0", port=Settings().gateway_port)  # noqa: S104
