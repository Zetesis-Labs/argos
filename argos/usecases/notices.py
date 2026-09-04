"""W1 · Apertura del caso de un aviso: el aviso, sus identificadores y su memoria
compartida entran en una sola transacción."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.analysis import DraftEntity
from argos.core.identifiers import extract_identifiers
from argos.core.ledger import ReusedCase, plan_notice_case
from argos.core.model import Case, Entity, entity_id
from argos.core.notices import Notice, NoticeRejected, validate_notice
from argos.core.ports import LedgerConflictError
from argos.usecases.deps import Bookkeeping


@dataclass(frozen=True)
class NoticeOpened:
    case: Case
    reused: bool


@dataclass(frozen=True)
class NoticeRefused:
    code: str


async def known_entities(
    services: Bookkeeping, entities: tuple[DraftEntity, ...]
) -> dict[str, Entity]:
    known: dict[str, Entity] = {}
    for drafted in entities:
        stored = await services.ledger.entity_by_value(drafted.kind, drafted.value)
        if stored is not None:
            known[entity_id(drafted.kind, drafted.value)] = stored
    return known


async def open_notice_case(
    services: Bookkeeping, *, tenant_id: str, notice: Notice, correlation_id: str
) -> NoticeOpened | NoticeRefused:
    ledger = services.ledger
    tenant = await ledger.tenant(tenant_id)
    if tenant is None or not tenant.active:
        return NoticeRefused("tenant.unknown")
    checked = validate_notice(notice, services.policy.notices)
    if isinstance(checked, NoticeRejected):
        return NoticeRefused(checked.code)
    now = services.clock.now()
    since = now - services.policy.retention.notice_dedup_window
    existing = await ledger.case_by_notice(tenant_id, checked.notice_hash, since=since)
    entities = extract_identifiers(notice.text, notice.links)
    plan = plan_notice_case(
        tenant_id=tenant_id,
        existing=existing,
        notice_hash=checked.notice_hash,
        notice_text=notice.text,
        notice_links=notice.links,
        language=notice.language_hint,
        entities=entities,
        known=await known_entities(services, entities),
        case_id=services.ids.new_id(),
        now=now,
        retention=services.policy.retention,
        correlation_id=correlation_id,
    )
    if isinstance(plan, ReusedCase):
        return await _reuse(services, plan.case_id)
    try:
        await ledger.commit(plan.ops)
    except LedgerConflictError:
        raced = await ledger.case_by_notice(tenant_id, checked.notice_hash, since=since)
        if raced is None:
            raise
        return NoticeOpened(case=raced, reused=True)
    return NoticeOpened(case=plan.case, reused=False)


async def _reuse(services: Bookkeeping, case_id: str) -> NoticeOpened | NoticeRefused:
    case = await services.ledger.case(case_id)
    if case is None:
        return NoticeRefused("case.not_found")
    return NoticeOpened(case=case, reused=True)
