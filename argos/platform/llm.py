"""Modelos a través de LiteLLM como endpoint compatible con OpenAI (constitución §11)."""

from __future__ import annotations

from agno.models.openai import OpenAIChat

from argos.config import Settings

MOCK_MODEL = "mock"


def build_model(settings: Settings, model_id: str) -> OpenAIChat:
    return OpenAIChat(
        id=model_id,
        base_url=f"{settings.litellm_base_url.rstrip('/')}/v1",
        api_key=settings.litellm_master_key.get_secret_value(),
    )
