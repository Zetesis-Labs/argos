"""W1 · Apertura del caso: el aviso, sus identificadores y su memoria entran en
una sola transacción."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.analysis import DraftEntity
from argos.core.identifiers import extract_identifiers
from argos.core.ledger import plan_notice_case
from argos.core.model import Case, Entity, entity_id
from argos.core.notices import Notice, NoticeRejected, validate_notice
from argos.usecases.deps import Bookkeeping


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


async def open_notice_case(services: Bookkeeping, notice: Notice) -> Case | NoticeRefused:
    checked = validate_notice(notice, services.policy.notices)
    if isinstance(checked, NoticeRejected):
        return NoticeRefused(checked.code)
    entities = extract_identifiers(notice.text, notice.links)
    plan = plan_notice_case(
        notice_text=notice.text,
        notice_links=notice.links,
        language=notice.language_hint,
        entities=entities,
        known=await known_entities(services, entities),
        case_id=services.ids.new_id(),
        now=services.clock.now(),
    )
    await services.ledger.commit(plan.ops)
    return plan.case
