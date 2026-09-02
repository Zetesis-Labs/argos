import pytest
from opentelemetry.sdk.trace import TracerProvider

from argos.config import Settings
from argos.platform.tracing import setup_tracing


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def tracing(settings: Settings) -> TracerProvider:
    return setup_tracing(settings, service_name="argos-tests")
