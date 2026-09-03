"""Frontera tipada para ejecutar agentes y equipos de Agno."""

from collections.abc import Awaitable
from typing import Protocol, cast


class AsyncAgent(Protocol):
    def arun(self, prompt: str, *, user_id: str, session_id: str) -> Awaitable[object]: ...


class RunOutput(Protocol):
    @property
    def content(self) -> object: ...


async def run_agent(agent: object, prompt: str, *, user_id: str, session_id: str) -> None:
    typed_agent = cast(AsyncAgent, agent)
    await typed_agent.arun(prompt, user_id=user_id, session_id=session_id)


async def run_text(runner: object, prompt: str, *, user_id: str, session_id: str) -> str:
    """El SDK declara `content` como `Any`; aquí se cruza esa frontera una sola vez."""
    typed_runner = cast(AsyncAgent, runner)
    output = cast(
        RunOutput, await typed_runner.arun(prompt, user_id=user_id, session_id=session_id)
    )
    content = output.content
    return content if isinstance(content, str) else ""
