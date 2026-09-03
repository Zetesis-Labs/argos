import asyncio
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import httpx
import pytest
from agno.agent import Agent
from opentelemetry.sdk.trace import TracerProvider

from argos.config import SecretValue, Settings
from argos.devtools.bootstrap_db import SCHEMA_VERSION, apply_schema
from argos.platform.agent import run_agent
from argos.platform.agno_db import build_agno_db
from argos.platform.llm import MOCK_MODEL, build_model, close_model
from argos.platform.mcp import (
    list_tool_names,
    mcp_session,
    result_has_error,
    result_text,
    run_query,
)
from argos.platform.surreal import JsonValue, SurrealError, SurrealHttp

pytestmark = pytest.mark.anyio

MCP_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "argos-tests", "version": "0"},
    },
}
MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


async def database_token(settings: Settings, *, ns: str, db: str, user: str, password: str) -> str:
    return await SurrealHttp(settings.surreal_url).sign_in(
        ns=ns,
        db=db,
        user=user,
        password=password,
    )


async def agent_token(settings: Settings) -> str:
    return await database_token(
        settings,
        ns=settings.ops_namespace,
        db=settings.ops_database,
        user=settings.surreal_agent_user,
        password=settings.surreal_agent_password.get_secret_value(),
    )


async def info_for_db(settings: Settings, ns: str, db: str) -> dict[str, JsonValue]:
    statements = await SurrealHttp(settings.surreal_url).sql(
        "INFO FOR DB;", auth=settings.root_auth, ns=ns, db=db
    )
    result = statements[-1].result
    assert isinstance(result, dict)
    return result


def names_in(section: JsonValue | None) -> set[str]:
    return set(section.keys()) if isinstance(section, dict) else set()


async def test_schema_bootstrap_is_idempotent(settings: Settings) -> None:
    """S01.1 el esquema se aplica de forma idempotente."""
    await apply_schema(settings)
    await apply_schema(settings)

    root_info = await SurrealHttp(settings.surreal_url).sql(
        "INFO FOR ROOT;", auth=settings.root_auth
    )
    root_result = root_info[-1].result
    assert isinstance(root_result, dict)
    namespaces = names_in(root_result.get("namespaces"))
    assert {settings.agno_namespace, settings.ops_namespace} <= namespaces

    ops = await info_for_db(settings, settings.ops_namespace, settings.ops_database)
    assert settings.surreal_agent_user in names_in(ops.get("users"))
    assert "schema_version" in names_in(ops.get("tables"))

    sessions = await info_for_db(settings, settings.agno_namespace, settings.agno_database)
    assert settings.surreal_runtime_user in names_in(sessions.get("users"))

    version = await SurrealHttp(settings.surreal_url).sql(
        "SELECT version, applied_at FROM schema_version:current;",
        auth=settings.root_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
    )
    rows = version[-1].result
    assert isinstance(rows, list) and rows
    row = rows[0]
    assert isinstance(row, dict)
    assert row.get("version") == SCHEMA_VERSION
    assert row.get("applied_at")

    rotated_agent_password = f"agent-{uuid4().hex}"
    rotated_runtime_password = f"runtime-{uuid4().hex}"
    rotated = replace(
        settings,
        surreal_agent_password=SecretValue(rotated_agent_password),
        surreal_runtime_password=SecretValue(rotated_runtime_password),
    )
    try:
        await apply_schema(rotated)
        await database_token(
            rotated,
            ns=rotated.ops_namespace,
            db=rotated.ops_database,
            user=rotated.surreal_agent_user,
            password=rotated_agent_password,
        )
        await database_token(
            rotated,
            ns=rotated.agno_namespace,
            db=rotated.agno_database,
            user=rotated.surreal_runtime_user,
            password=rotated_runtime_password,
        )
        with pytest.raises(SurrealError):
            await agent_token(settings)
    finally:
        await apply_schema(settings)


async def test_agent_user_enters_mcp_with_token(settings: Settings) -> None:
    """S01.2 el usuario de los agentes entra por MCP con token."""
    token = await agent_token(settings)
    async with mcp_session(settings.mcp_url, token=token) as session:
        tools = await list_tool_names(session)
    assert {"query", "select", "create", "relate", "info", "list"} <= tools


async def test_agent_user_cannot_leave_ops(settings: Settings) -> None:
    """S01.3 el usuario de los agentes no sale de argos/ops."""
    token = await agent_token(settings)
    async with mcp_session(settings.mcp_url, token=token) as session:
        crossing = await run_query(
            session, f"USE NS {settings.agno_namespace} DB {settings.agno_database}; INFO FOR DB;"
        )
    async with mcp_session(settings.mcp_url, token=token) as session:
        escalation = await run_query(
            session, "DEFINE USER intruder ON DATABASE PASSWORD 'x' ROLES OWNER;"
        )

    assert result_has_error(crossing)
    assert "Not enough permissions" in result_text(crossing)
    assert result_has_error(escalation)
    assert "Not enough permissions" in result_text(escalation)

    ops = await info_for_db(settings, settings.ops_namespace, settings.ops_database)
    assert "intruder" not in names_in(ops.get("users"))


async def test_anonymous_mcp_gets_no_data(settings: Settings) -> None:
    """S01.4 sin credenciales no hay datos."""
    async with mcp_session(settings.mcp_url, token=None) as session:
        result = await run_query(session, "SELECT * FROM schema_version;")
    assert result_has_error(result)
    assert "Anonymous access not allowed" in result_text(result)


async def test_mcp_accepts_compose_hostname(settings: Settings) -> None:
    """S01.5 el MCP acepta el hostname del compose."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            settings.mcp_url, json=MCP_INITIALIZE, headers=MCP_HEADERS, auth=settings.root_auth
        )
    assert response.status_code == 200, response.text[:200]


async def test_litellm_mock_model_reports_cost(settings: Settings) -> None:
    """S01.6 LiteLLM responde al modelo mock con coste y sin claves de proveedor."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{settings.litellm_base_url}/v1/chat/completions",
            json={"model": MOCK_MODEL, "messages": [{"role": "user", "content": "ping"}]},
            headers={"Authorization": f"Bearer {settings.litellm_master_key.get_secret_value()}"},
        )
    assert response.status_code == 200, response.text[:300]
    content = response.json()["choices"][0]["message"]["content"]
    assert "mock" in content.lower()
    assert float(response.headers["x-litellm-response-cost"]) > 0


async def test_minimal_agent_leaves_trace_in_langfuse(
    settings: Settings, tracing: TracerProvider
) -> None:
    """S01.7 un agente mínimo deja traza en Langfuse."""
    nonce = uuid4().hex
    user_id = f"s01-{nonce}"
    model = build_model(settings, MOCK_MODEL)
    agent = Agent(name="S01 smoke", model=model, telemetry=False)
    tracer = tracing.get_tracer("argos-tests")
    try:
        with tracer.start_as_current_span("s01-smoke") as root:
            root.set_attribute("langfuse.user.id", user_id)
            await run_agent(agent, f"ping {nonce}", user_id=user_id, session_id=f"session-{nonce}")
        trace_id = format(root.get_span_context().trace_id, "032x")
        tracing.force_flush()
    finally:
        await close_model(model)

    observations = await wait_for_observations(settings, trace_id=trace_id, timeout_seconds=60)
    assert observations, f"Langfuse no recibió ninguna observación de la traza {trace_id}"
    with_user = [
        user
        for observation in observations
        if isinstance((user := observation.get("userId")), str) and user
    ]
    assert with_user, "Langfuse no recibió el identificador de usuario"
    assert all(u == user_id for u in with_user), with_user


async def wait_for_observations(
    settings: Settings, *, trace_id: str, timeout_seconds: float
) -> list[dict[str, JsonValue]]:
    auth = (settings.langfuse_public_key, settings.langfuse_secret_key.get_secret_value())
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient(timeout=15, auth=auth) as client:
        while time.monotonic() < deadline:
            response = await client.get(
                f"{settings.langfuse_host}/api/public/v2/observations",
                params={"traceId": trace_id, "limit": 100},
            )
            if response.status_code == 200:
                observations = parse_observations(cast(object, response.json()))
                if observations:
                    return observations
            await asyncio.sleep(2)
    return []


def parse_observations(payload: object) -> list[dict[str, JsonValue]]:
    if not isinstance(payload, dict):
        return []
    data = cast(dict[str, object], payload).get("data")
    if not isinstance(data, list):
        return []
    observations: list[dict[str, JsonValue]] = []
    for item in cast(list[object], data):
        if isinstance(item, dict):
            observations.append(cast(dict[str, JsonValue], item))
    return observations


async def test_agno_sessions_live_only_in_agno_database(settings: Settings) -> None:
    """S01.8 Agno persiste sus sesiones en agno/sessions y nada en argos/ops."""
    session_id = f"s01-{uuid4().hex}"
    model = build_model(settings, MOCK_MODEL)
    agent = Agent(
        name="S01 sessions",
        model=model,
        db=build_agno_db(settings),
        telemetry=False,
    )
    try:
        await run_agent(agent, "ping", session_id=session_id, user_id="s01")
    finally:
        await close_model(model)

    sessions_db = await info_for_db(settings, settings.agno_namespace, settings.agno_database)
    agno_tables = {t for t in names_in(sessions_db.get("tables")) if t.startswith("agno_")}
    assert agno_tables, "Agno no creó ninguna tabla en agno/sessions"

    session_tables = [t for t in agno_tables if "session" in t]
    assert session_tables
    stored = await SurrealHttp(settings.surreal_url).sql(
        f"SELECT session_id FROM {session_tables[0]} WHERE record::id(id) = '{session_id}';",
        auth=settings.root_auth,
        ns=settings.agno_namespace,
        db=settings.agno_database,
    )
    assert stored[-1].result, f"la sesión {session_id} no está en {session_tables[0]}"

    ops = await info_for_db(settings, settings.ops_namespace, settings.ops_database)
    assert not {t for t in names_in(ops.get("tables")) if t.startswith("agno_")}


async def test_published_dev_ports_bind_only_to_loopback() -> None:
    """S01.10 los servicios publicados por el devcontenedor solo escuchan en loopback."""
    compose = Path(".devcontainer/docker-compose.yml").read_text(encoding="utf-8")
    published_ports = re.findall(r'^\s+-\s+"([^"\n]+:\d+)"\s*$', compose, re.MULTILINE)
    assert published_ports
    assert all(mapping.startswith("127.0.0.1:") for mapping in published_ports), published_ports
