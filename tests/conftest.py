from dataclasses import replace

import pytest
from opentelemetry.sdk.trace import TracerProvider

from argos.config import Settings
from argos.platform.llm import MOCK_MODEL
from argos.platform.tracing import setup_tracing


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return replace(
        Settings(),
        surreal_url="http://surrealdb-test:8000",
        nats_url="nats://nats-test:4222",
        analysis_model=MOCK_MODEL,
    )


@pytest.fixture(scope="session")
def tracing(settings: Settings) -> TracerProvider:
    return setup_tracing(settings, service_name="argos-tests")
