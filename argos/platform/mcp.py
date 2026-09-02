"""Sesiones MCP contra el servidor embebido de SurrealDB (transporte streamable-http)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent


def bearer_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


@asynccontextmanager
async def mcp_session(url: str, *, token: str | None) -> AsyncGenerator[ClientSession]:
    timeout = httpx.Timeout(30.0, read=300.0)
    async with (
        httpx.AsyncClient(
            headers=bearer_headers(token), timeout=timeout, follow_redirects=True
        ) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


async def list_tool_names(session: ClientSession) -> set[str]:
    listed = await session.list_tools()
    return {tool.name for tool in listed.tools}


async def run_query(session: ClientSession, query: str) -> CallToolResult:
    return await session.call_tool("query", {"query": query})


def result_text(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def result_has_error(result: CallToolResult) -> bool:
    if result.isError:
        return True
    structured = cast(object, result.structuredContent)
    if not isinstance(structured, dict):
        return False
    fields = cast(dict[str, object], structured)
    return bool(fields.get("has_errors")) or fields.get("status") == "error"
