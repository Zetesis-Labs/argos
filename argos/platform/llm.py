"""Modelos a través de LiteLLM como endpoint compatible con OpenAI (constitución §11)."""

from __future__ import annotations

from typing import Protocol, cast

from agno.models.openai import OpenAIChat

from argos.config import Settings

MOCK_MODEL = "mock"


class _AsyncClient(Protocol):
    async def close(self) -> None: ...


def build_model(settings: Settings, model_id: str) -> OpenAIChat:
    return OpenAIChat(
        id=model_id,
        base_url=f"{settings.litellm_base_url.rstrip('/')}/v1",
        api_key=settings.litellm_master_key.get_secret_value(),
    )


async def close_model(model: OpenAIChat) -> None:
    """El SDK abre su propio cliente HTTP; sin cerrarlo lo hace el recolector, ya
    fuera del bucle de eventos que lo creó."""
    client = cast(_AsyncClient | None, model.async_client)
    if client is not None:
        await client.close()
