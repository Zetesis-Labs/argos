"""Fakes en memoria de los puertos. Reproducen las mismas garantías que los adaptadores reales."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from argos.core.knowledge import KnowledgeBundle, KnowledgeSnapshot
from argos.core.model import (
    Case,
    CaseEntity,
    Delete,
    Entity,
    EntityKind,
    EntityLink,
    Insert,
    LedgerOp,
    LedgerRecord,
    OfficialWarning,
    Signal,
    Tenant,
    Verdict,
    VerdictState,
    table_name,
)
from argos.core.ports import (
    CaseBrief,
    Investigation,
    LedgerConflictError,
    VerdictBrief,
)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class SequentialIds:
    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._next = 0

    def new_id(self) -> str:
        self._next += 1
        return f"{self._prefix}{self._next:04d}"


class InMemoryKnowledgeProjection:
    def __init__(self) -> None:
        self.current: KnowledgeSnapshot | None = None
        self.nodes: tuple[object, ...] = ()
        self.edges: tuple[object, ...] = ()
        self.warnings: tuple[OfficialWarning, ...] = ()
        self.writes = 0
        self.fail_next = False

    async def activate(
        self,
        snapshot: KnowledgeSnapshot,
        bundle: KnowledgeBundle,
        warnings: Sequence[OfficialWarning],
    ) -> bool:
        if (
            self.current is not None
            and self.current.content_hash == snapshot.content_hash
            and self.current.projection_version == snapshot.projection_version
        ):
            return False
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("fallo de almacenamiento")
        self.current = snapshot
        self.nodes = bundle.nodes
        self.edges = bundle.edges
        self.warnings = tuple(warnings)
        self.writes += 1
        return True


SHARED_RECORDS = (Entity, EntityLink, OfficialWarning)


def _unique_key(record: LedgerRecord) -> tuple[object, ...] | None:
    match record:
        case Entity():
            return ("entity", record.kind, record.value)
        case EntityLink():
            return ("entity_link", record.left_entity_id, record.right_entity_id)
        case CaseEntity():
            return ("case_entity", record.case_id, record.entity_id)
        case Verdict():
            return ("verdict", record.case_id, record.version)
        case _:
            return None


class InMemoryLedger:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], LedgerRecord] = {}

    def _key(self, record: LedgerRecord) -> tuple[str, str]:
        return (table_name(record), record.id)

    def _check(self, ops: Sequence[LedgerOp]) -> None:
        planned_uniques: set[tuple[object, ...]] = set()
        planned_keys: set[tuple[str, str]] = set()
        for op in ops:
            key = self._key(op.record)
            if key in planned_keys:
                raise LedgerConflictError(f"{key} written twice in one transaction")
            planned_keys.add(key)
            if isinstance(op, Delete):
                continue
            if isinstance(op, Insert):
                if key in self._rows:
                    raise LedgerConflictError(f"{key} already exists")
                unique = _unique_key(op.record)
                if unique is not None:
                    if unique in planned_uniques or any(
                        _unique_key(row) == unique for row in self._rows.values()
                    ):
                        raise LedgerConflictError(f"unique violation {unique}")
                    planned_uniques.add(unique)
            else:
                stored = self._rows.get(key)
                if stored is None or stored.revision != op.record.revision - 1:
                    raise LedgerConflictError(f"{key} revision mismatch")

    async def commit(self, ops: Sequence[LedgerOp]) -> None:
        self._check(ops)
        for op in ops:
            if isinstance(op, Delete):
                self._rows.pop(self._key(op.record), None)
            else:
                self._rows[self._key(op.record)] = op.record

    def _all(self, table: str) -> list[LedgerRecord]:
        return [row for (name, _), row in self._rows.items() if name == table]

    async def tenant(self, tenant_id: str) -> Tenant | None:
        row = self._rows.get(("tenant", tenant_id))
        return row if isinstance(row, Tenant) else None

    async def case(self, case_id: str) -> Case | None:
        row = self._rows.get(("case", case_id))
        return row if isinstance(row, Case) else None

    async def case_by_notice(
        self, tenant_id: str, notice_hash: str, *, since: datetime
    ) -> Case | None:
        matches = [
            row
            for row in self._all("case")
            if isinstance(row, Case)
            and row.tenant_id == tenant_id
            and row.notice_hash == notice_hash
            and row.created_at >= since
        ]
        matches.sort(key=lambda row: row.created_at, reverse=True)
        return matches[0] if matches else None

    async def entity_by_value(self, kind: EntityKind, value: str) -> Entity | None:
        for row in self._all("entity"):
            if isinstance(row, Entity) and row.kind is kind and row.value == value:
                return row
        return None

    async def entities_of_case(self, case_id: str) -> list[CaseEntity]:
        links = [
            row
            for row in self._all("case_entity")
            if isinstance(row, CaseEntity) and row.case_id == case_id
        ]
        links.sort(key=lambda row: row.created_at)
        return links

    async def cases_of_entity(self, entity_id: str) -> list[CaseEntity]:
        links = [
            row
            for row in self._all("case_entity")
            if isinstance(row, CaseEntity) and row.entity_id == entity_id
        ]
        links.sort(key=lambda row: row.created_at)
        return links

    async def warning(self, warning_id: str) -> OfficialWarning | None:
        row = self._rows.get(("warning", warning_id))
        return row if isinstance(row, OfficialWarning) else None

    async def warnings_for(self, kind: EntityKind, value: str) -> list[OfficialWarning]:
        warnings = [
            row
            for row in self._all("warning")
            if isinstance(row, OfficialWarning)
            and row.entity_kind is kind
            and row.entity_value == value
        ]
        warnings.sort(key=lambda row: row.captured_at)
        return warnings

    async def signals_of_case(self, case_id: str) -> list[Signal]:
        signals = [
            row for row in self._all("signal") if isinstance(row, Signal) and row.case_id == case_id
        ]
        signals.sort(key=lambda row: (row.created_at, row.id))
        return signals

    async def current_verdict(self, case_id: str) -> Verdict | None:
        verdicts = [
            row
            for row in self._all("verdict")
            if isinstance(row, Verdict)
            and row.case_id == case_id
            and row.state is VerdictState.CURRENT
        ]
        verdicts.sort(key=lambda row: row.version, reverse=True)
        return verdicts[0] if verdicts else None

    async def delete_tenant_data(self, tenant_id: str) -> None:
        doomed = [key for key, row in self._rows.items() if _belongs_to(row, tenant_id)]
        for key in doomed:
            del self._rows[key]


def _belongs_to(record: LedgerRecord, tenant_id: str) -> bool:
    if isinstance(record, Tenant):
        return record.id == tenant_id
    if isinstance(record, SHARED_RECORDS):
        return False
    return record.tenant_id == tenant_id


class ScriptedInvestigator:
    """Devuelve una investigación fija y guarda lo que se le pidió."""

    def __init__(self, result: Investigation) -> None:
        self._result = result
        self.briefs: list[CaseBrief] = []

    async def investigate(self, brief: CaseBrief) -> Investigation:
        self.briefs.append(brief)
        return self._result


class ScriptedNarrator:
    def __init__(self, summary: str = "Resumen de prueba con indicios.") -> None:
        self._summary = summary
        self.briefs: list[VerdictBrief] = []

    async def narrate(self, brief: VerdictBrief) -> str:
        self.briefs.append(brief)
        return self._summary
