"""Proyección atómica de un bundle OKF sobre SurrealDB."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, cast

from surrealdb import AsyncSurreal

from argos.config import Settings
from argos.core.knowledge import KnowledgeBundle, KnowledgeSnapshot
from argos.core.model import OfficialWarning
from argos.core.ports import LedgerError
from argos.platform.ledger import Params, params_of
from argos.platform.rows import Row, to_row

SKIPPED_MARKERS = ("not executed", "cancelled transaction", "Cannot COMMIT")


class KnowledgeDatabase(Protocol):
    async def signin(self, credentials: dict[str, str]) -> object: ...

    async def use(self, namespace: str, database: str) -> object: ...

    async def query_raw(self, query: str, variables: Params | None = None) -> object: ...

    async def close(self) -> object: ...


def _row_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:32]}"


def _results(raw: object) -> list[object]:
    if not isinstance(raw, dict):
        raise LedgerError(f"unexpected response {raw!r}")
    results = cast(dict[str, object], raw).get("result")
    if not isinstance(results, list):
        raise LedgerError(f"unexpected response {raw!r}")
    return cast(list[object], results)


def _raise_for_error(raw: object) -> None:
    deferred_error: str | None = None
    for item in _results(raw):
        if not isinstance(item, dict):
            raise LedgerError(f"unexpected statement {item!r}")
        statement = cast(dict[str, object], item)
        if statement.get("status") == "OK":
            continue
        message = str(statement.get("result"))
        if any(marker in message for marker in SKIPPED_MARKERS):
            deferred_error = deferred_error or message
            continue
        raise LedgerError(message)
    if deferred_error is not None:
        raise LedgerError(deferred_error)


def _last_rows(raw: object) -> list[Row]:
    _raise_for_error(raw)
    results = _results(raw)
    if not results:
        return []
    last = results[-1]
    if not isinstance(last, dict):
        raise LedgerError(f"unexpected statement {last!r}")
    value = cast(dict[str, object], last).get("result")
    if not isinstance(value, list):
        raise LedgerError(f"unexpected rows {value!r}")
    rows: list[Row] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            raise LedgerError(f"unexpected row {item!r}")
        rows.append(cast(Row, item))
    return rows


def _snapshot(row: Row) -> KnowledgeSnapshot:
    source_head = row.get("source_head")
    content_hash = row.get("content_hash")
    graph_schema = row.get("graph_schema")
    profile = row.get("profile")
    projection_version = row.get("projection_version")
    imported_at = row.get("imported_at")
    node_count = row.get("node_count")
    edge_count = row.get("edge_count")
    warning_count = row.get("warning_count")
    if not all(
        isinstance(value, str)
        for value in (source_head, content_hash, graph_schema, profile)
    ):
        raise LedgerError("knowledge snapshot has invalid text fields")
    if not isinstance(imported_at, datetime):
        raise LedgerError("knowledge snapshot has invalid imported_at")
    if not all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (projection_version, node_count, edge_count, warning_count)
    ):
        raise LedgerError("knowledge snapshot has invalid counts")
    return KnowledgeSnapshot(
        source_head=cast(str, source_head),
        content_hash=cast(str, content_hash),
        graph_schema=cast(str, graph_schema),
        profile=cast(str, profile),
        projection_version=cast(int, projection_version),
        imported_at=imported_at,
        node_count=cast(int, node_count),
        edge_count=cast(int, edge_count),
        warning_count=cast(int, warning_count),
    )


class SurrealKnowledgeProjection:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _connect(self) -> KnowledgeDatabase:
        database = cast(
            KnowledgeDatabase,
            AsyncSurreal(f"{self._settings.surreal_ws_url}/rpc"),
        )
        await database.signin(
            {
                "username": self._settings.surreal_root_user,
                "password": self._settings.surreal_root_password.get_secret_value(),
            }
        )
        await database.use(self._settings.ops_namespace, self._settings.ops_database)
        return database

    async def current_snapshot(self) -> KnowledgeSnapshot | None:
        database = await self._connect()
        try:
            raw = await database.query_raw(
                "SELECT * FROM knowledge_snapshot:current;"
            )
            rows = _last_rows(raw)
            return _snapshot(rows[0]) if rows else None
        finally:
            await database.close()

    async def activate(
        self,
        snapshot: KnowledgeSnapshot,
        bundle: KnowledgeBundle,
        warnings: Sequence[OfficialWarning],
    ) -> bool:
        database = await self._connect()
        try:
            current_raw = await database.query_raw(
                    "SELECT content_hash, projection_version FROM knowledge_snapshot:current;"
            )
            current = _last_rows(current_raw)
            if (
                current
                and current[0].get("content_hash") == snapshot.content_hash
                and current[0].get("projection_version") == snapshot.projection_version
            ):
                return False

            statements = [
                "BEGIN TRANSACTION;",
                "DELETE knowledge_node;",
                "DELETE knowledge_edge;",
                "DELETE warning;",
            ]
            params: Row = {}
            nodes_by_slug = {node.slug: node for node in bundle.nodes}
            for index, node in enumerate(bundle.nodes):
                params[f"ni{index}"] = _row_id("node", node.knowledge_id)
                params[f"nc{index}"] = {
                    "knowledge_id": node.knowledge_id,
                    "slug": node.slug,
                    "title": node.title,
                    "node_type": node.kind,
                    "description": node.description,
                    "path": node.path,
                    "properties_json": json.dumps(
                        node.properties, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                    "source_head": snapshot.source_head,
                }
                statements.append(
                    f"CREATE type::record('knowledge_node', $ni{index}) CONTENT $nc{index};"
                )
            for index, edge in enumerate(bundle.edges):
                source_id = nodes_by_slug[edge.source].knowledge_id
                target_id = nodes_by_slug[edge.target].knowledge_id
                identity = f"{source_id}\n{edge.label}\n{target_id}"
                params[f"ei{index}"] = _row_id("edge", identity)
                params[f"ec{index}"] = {
                    "source_knowledge_id": source_id,
                    "target_knowledge_id": target_id,
                    "source_slug": edge.source,
                    "target_slug": edge.target,
                    "label": edge.label,
                    "derived": edge.derived,
                    "source_head": snapshot.source_head,
                }
                statements.append(
                    f"CREATE type::record('knowledge_edge', $ei{index}) CONTENT $ec{index};"
                )
            for index, warning in enumerate(warnings):
                params[f"wi{index}"] = warning.id
                params[f"wc{index}"] = to_row(warning)
                statements.append(
                    f"CREATE type::record('warning', $wi{index}) CONTENT $wc{index};"
                )
            params["snapshot"] = {
                "source_head": snapshot.source_head,
                "content_hash": snapshot.content_hash,
                "graph_schema": snapshot.graph_schema,
                "profile": snapshot.profile,
                "projection_version": snapshot.projection_version,
                "imported_at": snapshot.imported_at,
                "node_count": snapshot.node_count,
                "edge_count": snapshot.edge_count,
                "warning_count": snapshot.warning_count,
            }
            statements.extend(
                [
                    "UPSERT knowledge_snapshot:current CONTENT $snapshot;",
                    "COMMIT TRANSACTION;",
                ]
            )
            raw = await database.query_raw(
                "\n".join(statements), params_of(params)
            )
            _raise_for_error(raw)
            return True
        finally:
            await database.close()
