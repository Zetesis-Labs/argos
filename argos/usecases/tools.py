"""Herramientas de negocio de los agentes. Solo lectura, por capacidad, tenant y
caso. Ninguna entrega SurrealQL general ni credenciales internas."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.agents import AgentName, Capability, allows
from argos.core.analysis import CaseAppearance, EntityHistory, aggregate_history
from argos.core.identifiers import normalized_identifier
from argos.core.model import CaseState, EntityKind
from argos.usecases.deps import Bookkeeping
from argos.usecases.queries import VerdictSummary, summary_of

NOT_AUTHORIZED = "tool.not_authorized"
CASE_NOT_FOUND = "case.not_found"


@dataclass(frozen=True)
class ToolCaller:
    agent: AgentName
    tenant_id: str
    case_id: str


@dataclass(frozen=True)
class ToolDenied:
    code: str


@dataclass(frozen=True)
class CaseContext:
    case_id: str
    state: CaseState
    language: str | None
    verdict: VerdictSummary | None


@dataclass(frozen=True)
class RegistryMatch:
    regulator: str
    url: str
    captured_at: str
    active: bool


async def _authorized_case(
    services: Bookkeeping, caller: ToolCaller, capability: Capability
) -> ToolDenied | None:
    if not allows(caller.agent, capability):
        return ToolDenied(NOT_AUTHORIZED)
    case = await services.ledger.case(caller.case_id)
    if case is None or case.tenant_id != caller.tenant_id:
        return ToolDenied(CASE_NOT_FOUND)
    return None


async def get_case_context(services: Bookkeeping, caller: ToolCaller) -> CaseContext | ToolDenied:
    denied = await _authorized_case(services, caller, Capability.GET_CASE_CONTEXT)
    if denied is not None:
        return denied
    case = await services.ledger.case(caller.case_id)
    if case is None:
        return ToolDenied(CASE_NOT_FOUND)
    return CaseContext(
        case_id=case.id,
        state=case.state,
        language=case.language,
        verdict=summary_of(await services.ledger.current_verdict(case.id)),
    )


async def find_registry_matches(
    services: Bookkeeping, caller: ToolCaller, *, kind: EntityKind, value: str
) -> tuple[RegistryMatch, ...] | ToolDenied:
    if not allows(caller.agent, Capability.FIND_REGISTRY_MATCHES):
        return ToolDenied(NOT_AUTHORIZED)
    warnings = await services.ledger.warnings_for(kind, normalized_identifier(kind, value))
    return tuple(
        RegistryMatch(
            regulator=warning.regulator,
            url=warning.url,
            captured_at=warning.captured_at.isoformat(),
            active=warning.active,
        )
        for warning in warnings
    )


async def find_entity_history(
    services: Bookkeeping, caller: ToolCaller, *, kind: EntityKind, value: str
) -> EntityHistory | ToolDenied:
    """R29: al tenant solo le llegan agregados, nunca casos, citas ni tenants ajenos."""
    if not allows(caller.agent, Capability.FIND_ENTITY_HISTORY):
        return ToolDenied(NOT_AUTHORIZED)
    normalized = normalized_identifier(kind, value)
    entity = await services.ledger.entity_by_value(kind, normalized)
    if entity is None:
        return aggregate_history(kind, normalized, ())
    appearances: list[CaseAppearance] = []
    for link in await services.ledger.cases_of_entity(entity.id):
        case = await services.ledger.case(link.case_id)
        if case is None:
            continue
        appearances.append(
            CaseAppearance(
                case_id=case.id,
                tenant_id=case.tenant_id,
                review_state=case.review_state,
                seen_at=case.created_at,
            )
        )
    return aggregate_history(entity.kind, entity.value, appearances)
