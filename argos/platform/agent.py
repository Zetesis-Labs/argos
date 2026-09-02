"""Frontera tipada para ejecutar agentes de Agno."""

from collections.abc import Awaitable
from typing import Protocol, cast


class AsyncAgent(Protocol):
    def arun(self, prompt: str, *, user_id: str, session_id: str) -> Awaitable[object]: ...


async def run_agent(agent: object, prompt: str, *, user_id: str, session_id: str) -> None:
    typed_agent = cast(AsyncAgent, agent)
    await typed_agent.arun(prompt, user_id=user_id, session_id=session_id)
