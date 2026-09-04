from dataclasses import replace

import pytest

from argos.config import Settings
from argos.platform.llm import MOCK_MODEL


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return replace(
        Settings(),
        surreal_url="http://surrealdb-test:8000",
        analysis_model=MOCK_MODEL,
    )
