"""Libro operacional sobre SurrealDB: transacciones con escrituras condicionales."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from surrealdb import AsyncSurreal
from surrealdb.types import Value

from argos.config import Settings
from argos.core.model import (
    Case,
    CaseEntity,
    Delete,
    Entity,
    EntityKind,
    Insert,
    LedgerOp,
    LedgerRecord,
    OfficialWarning,
    Signal,
    Verdict,
    VerdictState,
    table_name,
)
from argos.core.ports import LedgerConflictError, LedgerError
from argos.platform.rows import Row, from_row, to_row

Params = dict[str, Value]

CONFLICT_MARKERS = ("conflict", "already contains", "already exists")
SKIPPED_MARKERS = ("not executed", "cancelled transaction", "Cannot COMMIT")

DELETE_CASE_STATEMENTS = (
    "DELETE FROM case_entity WHERE case_id = $case;",
    "DELETE FROM signal WHERE case_id = $case;",
    "DELETE FROM verdict WHERE case_id = $case;",
    "DELETE type::record('case', $case);",
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
            elif isinstance(op, Delete):
                statements.append(f"DELETE type::record($t{index}, $i{index});")
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

    async def case(self, case_id: str) -> Case | None:
        return await self._one(Case, "case", case_id)

    async def entity_by_value(self, kind: EntityKind, value: str) -> Entity | None:
        entities = await self._many(
            Entity,
            "SELECT * FROM entity WHERE kind = $kind AND value = $value LIMIT 1;",
            {"kind": kind.value, "value": value},
        )
        return entities[0] if entities else None

    async def entities_of_case(self, case_id: str) -> list[CaseEntity]:
        return await self._many(
            CaseEntity,
            "SELECT * FROM case_entity WHERE case_id = $case ORDER BY created_at;",
            {"case": case_id},
        )

    async def cases_of_entity(self, entity_id: str) -> list[CaseEntity]:
        return await self._many(
            CaseEntity,
            "SELECT * FROM case_entity WHERE entity_id = $entity ORDER BY created_at;",
            {"entity": entity_id},
        )

    async def warning(self, warning_id: str) -> OfficialWarning | None:
        return await self._one(OfficialWarning, "warning", warning_id)

    async def warnings_for(self, kind: EntityKind, value: str) -> list[OfficialWarning]:
        return await self._many(
            OfficialWarning,
            "SELECT * FROM warning WHERE entity_kind = $kind AND entity_value = $value "
            "ORDER BY captured_at;",
            {"kind": kind.value, "value": value},
        )

    async def signals_of_case(self, case_id: str) -> list[Signal]:
        return await self._many(
            Signal,
            "SELECT * FROM signal WHERE case_id = $case ORDER BY created_at, id;",
            {"case": case_id},
        )

    async def current_verdict(self, case_id: str) -> Verdict | None:
        verdicts = await self._many(
            Verdict,
            "SELECT * FROM verdict WHERE case_id = $case AND state = $state "
            "ORDER BY version DESC LIMIT 1;",
            {"case": case_id, "state": VerdictState.CURRENT.value},
        )
        return verdicts[0] if verdicts else None

    async def delete_case(self, case_id: str) -> None:
        await self._query("\n".join(DELETE_CASE_STATEMENTS), {"case": case_id})


def ledger_for(settings: Settings, workload: str) -> SurrealLedger:
    """Cada proceso entra con su propia identidad (S02 §7)."""
    credentials = settings.workload(workload)
    return SurrealLedger(
        url=f"{settings.surreal_ws_url}/rpc",
        namespace=settings.ops_namespace,
        database=settings.ops_database,
        user=credentials.user,
        password=credentials.password.get_secret_value(),
    )
