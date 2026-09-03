"""Libro de trabajos sobre SurrealDB: transacciones con escrituras condicionales (S02 §6)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from surrealdb import AsyncSurreal
from surrealdb.types import Value

from argos.core.model import (
    Artifact,
    Attempt,
    Case,
    Chunk,
    Document,
    Extraction,
    Insert,
    Job,
    LedgerOp,
    LedgerRecord,
    OutboxEntry,
    Tenant,
    table_name,
)
from argos.core.ports import LedgerConflictError, LedgerError
from argos.platform.rows import Row, from_row, to_row

Params = dict[str, Value]

CONFLICT_MARKERS = ("conflict", "already contains", "already exists")
SKIPPED_MARKERS = ("not executed", "cancelled transaction", "Cannot COMMIT")

DELETE_TENANT_STATEMENTS = (
    "DELETE FROM case WHERE tenant_id = $tenant;",
    "DELETE FROM artifact WHERE tenant_id = $tenant;",
    "DELETE FROM document WHERE tenant_id = $tenant;",
    "DELETE FROM job WHERE tenant_id = $tenant;",
    "DELETE FROM attempt WHERE tenant_id = $tenant;",
    "DELETE FROM outbox_entry WHERE tenant_id = $tenant;",
    "DELETE FROM extraction WHERE tenant_id = $tenant;",
    "DELETE FROM chunk WHERE tenant_id = $tenant;",
    "DELETE type::record('tenant', $tenant);",
)


def params_of(row: Row) -> Params:
    return {name: cast(Value, value) for name, value in row.items()}


def _statement_error(raw: object) -> str | None:
    if not isinstance(raw, dict):
        raise LedgerError(f"unexpected response {raw!r}")
    response = cast(dict[str, object], raw)
    results = response.get("result")
    if not isinstance(results, list):
        raise LedgerError(f"unexpected response {raw!r}")
    for item in cast(list[object], results):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, object], item)
        if entry.get("status") == "OK":
            continue
        message = str(entry.get("result"))
        if any(marker in message for marker in SKIPPED_MARKERS):
            continue
        return message
    return None


def _rows(raw: object) -> list[Row]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LedgerError(f"unexpected query result {raw!r}")
    rows: list[Row] = []
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            raise LedgerError(f"unexpected row {item!r}")
        rows.append(cast(Row, item))
    return rows


class SurrealLedger:
    def __init__(
        self, *, url: str, namespace: str, database: str, user: str, password: str
    ) -> None:
        self._url = url
        self._namespace = namespace
        self._database = database
        self._user = user
        self._password = password
        self._db = AsyncSurreal(url)

    async def connect(self) -> None:
        await self._db.signin(
            {
                "namespace": self._namespace,
                "database": self._database,
                "username": self._user,
                "password": self._password,
            }
        )
        await self._db.use(self._namespace, self._database)

    async def close(self) -> None:
        await self._db.close()

    async def _query(self, sql: str, params: Row) -> list[Row]:
        raw = cast(object, await self._db.query_raw(sql, params_of(params)))
        error = _statement_error(raw)
        if error is not None:
            raise LedgerError(error)
        response = cast(dict[str, object], raw)
        results = cast(list[object], response["result"])
        last = cast(dict[str, object], results[-1])
        return _rows(last.get("result"))

    async def commit(self, ops: Sequence[LedgerOp]) -> None:
        if not ops:
            return
        statements = ["BEGIN TRANSACTION;"]
        params: Row = {}
        for index, op in enumerate(ops):
            params[f"t{index}"] = table_name(op.record)
            params[f"i{index}"] = op.record.id
            params[f"c{index}"] = to_row(op.record)
            if isinstance(op, Insert):
                statements.append(f"CREATE type::record($t{index}, $i{index}) CONTENT $c{index};")
            else:
                params[f"r{index}"] = op.record.revision - 1
                statements.append(
                    f"LET $u{index} = UPDATE type::record($t{index}, $i{index}) "
                    f"CONTENT $c{index} WHERE revision = $r{index} RETURN AFTER; "
                    f"IF array::len($u{index}) = 0 {{ THROW 'conflict' }};"
                )
        statements.append("COMMIT TRANSACTION;")
        raw = cast(object, await self._db.query_raw("\n".join(statements), params_of(params)))
        error = _statement_error(raw)
        if error is None:
            return
        if any(marker in error for marker in CONFLICT_MARKERS):
            raise LedgerConflictError(error)
        raise LedgerError(error)

    async def _one[R: LedgerRecord](self, cls: type[R], table: str, record_id: str) -> R | None:
        rows = await self._query(
            "SELECT * FROM type::record($t, $id);", {"t": table, "id": record_id}
        )
        return from_row(cls, rows[0]) if rows else None

    async def _many[R: LedgerRecord](self, cls: type[R], sql: str, params: Row) -> list[R]:
        return [from_row(cls, row) for row in await self._query(sql, params)]

    async def tenant(self, tenant_id: str) -> Tenant | None:
        return await self._one(Tenant, "tenant", tenant_id)

    async def case(self, case_id: str) -> Case | None:
        return await self._one(Case, "case", case_id)

    async def case_by_notice(
        self, tenant_id: str, notice_hash: str, *, since: datetime
    ) -> Case | None:
        cases = await self._many(
            Case,
            "SELECT * FROM case WHERE tenant_id = $tenant AND notice_hash = $hash "
            "AND created_at >= $since ORDER BY created_at DESC LIMIT 1;",
            {"tenant": tenant_id, "hash": notice_hash, "since": since},
        )
        return cases[0] if cases else None

    async def artifact(self, artifact_id: str) -> Artifact | None:
        return await self._one(Artifact, "artifact", artifact_id)

    async def document(self, document_id: str) -> Document | None:
        return await self._one(Document, "document", document_id)

    async def document_by_hash(self, case_id: str, sha256: str) -> Document | None:
        documents = await self._many(
            Document,
            "SELECT * FROM document WHERE case_id = $case AND sha256 = $hash LIMIT 1;",
            {"case": case_id, "hash": sha256},
        )
        return documents[0] if documents else None

    async def job(self, job_id: str) -> Job | None:
        return await self._one(Job, "job", job_id)

    async def jobs_of_case(self, case_id: str) -> list[Job]:
        return await self._many(
            Job, "SELECT * FROM job WHERE case_id = $case ORDER BY created_at;", {"case": case_id}
        )

    async def jobs_with_expired_lease(self, now: datetime) -> list[Job]:
        return await self._many(
            Job,
            "SELECT * FROM job WHERE state = 'running' AND lease_until != NONE "
            "AND lease_until < $now;",
            {"now": now},
        )

    async def attempts(self, job_id: str) -> list[Attempt]:
        return await self._many(
            Attempt, "SELECT * FROM attempt WHERE job_id = $job ORDER BY number;", {"job": job_id}
        )

    async def outbox_entry(self, entry_id: str) -> OutboxEntry | None:
        return await self._one(OutboxEntry, "outbox_entry", entry_id)

    async def outbox_of_job(self, job_id: str) -> list[OutboxEntry]:
        return await self._many(
            OutboxEntry,
            "SELECT * FROM outbox_entry WHERE job_id = $job ORDER BY created_at, id;",
            {"job": job_id},
        )

    async def pending_outbox(self, now: datetime, *, limit: int) -> list[OutboxEntry]:
        return await self._many(
            OutboxEntry,
            "SELECT * FROM outbox_entry WHERE state = 'pending' AND not_before <= $now "
            "AND (lease_until = NONE OR lease_until <= $now) ORDER BY not_before, id LIMIT $limit;",
            {"now": now, "limit": limit},
        )

    async def extraction(self, extraction_id: str) -> Extraction | None:
        return await self._one(Extraction, "extraction", extraction_id)

    async def extractions_of_document(self, document_id: str) -> list[Extraction]:
        return await self._many(
            Extraction,
            "SELECT * FROM extraction WHERE document_id = $document ORDER BY created_at;",
            {"document": document_id},
        )

    async def chunks(self, extraction_id: str) -> list[Chunk]:
        return await self._many(
            Chunk,
            "SELECT * FROM chunk WHERE extraction_id = $extraction ORDER BY position;",
            {"extraction": extraction_id},
        )

    async def delete_tenant_data(self, tenant_id: str) -> None:
        await self._query("\n".join(DELETE_TENANT_STATEMENTS), {"tenant": tenant_id})
