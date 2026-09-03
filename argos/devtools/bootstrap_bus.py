"""Declara streams y consumidores durables de JetStream de forma idempotente (S02 §8)."""

from __future__ import annotations

import asyncio
import sys

from argos.config import Settings
from argos.core.policy import Policy
from argos.platform.bus import JetStreamBus, TopologyState


async def declare_topology(settings: Settings, policy: Policy) -> TopologyState:
    bus = JetStreamBus(settings.nats_url, policy=policy.jobs)
    await bus.connect()
    try:
        await bus.declare()
        return await bus.describe()
    finally:
        await bus.close()


def render(state: TopologyState) -> str:
    lines = [f"stream {stream.name}: {len(stream.subjects)} subjects" for stream in state.streams]
    lines.extend(
        f"consumer {consumer.durable} on {consumer.stream}" for consumer in state.consumers
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    state = asyncio.run(declare_topology(Settings(), Policy()))
    sys.stdout.write(render(state))
