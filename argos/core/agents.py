"""Catálogo de agentes y sus capacidades (constitución §8).

Todas las capacidades son de lectura: mover un caso o escribir una señal es un
caso de uso, nunca una herramienta de un agente.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class AgentName(StrEnum):
    TRIAGE = "triage_agent"
    PATTERNS = "patterns_agent"
    VERDICT_WRITER = "verdict_writer"
    CONVERSATION = "conversation_agent"


class Capability(StrEnum):
    GET_CASE_CONTEXT = "get_case_context"
    FIND_REGISTRY_MATCHES = "find_registry_matches"
    FIND_ENTITY_HISTORY = "find_entity_history"


CAPABILITIES: Mapping[AgentName, frozenset[Capability]] = {
    AgentName.TRIAGE: frozenset(
        {
            Capability.GET_CASE_CONTEXT,
            Capability.FIND_REGISTRY_MATCHES,
            Capability.FIND_ENTITY_HISTORY,
        }
    ),
    AgentName.PATTERNS: frozenset({Capability.GET_CASE_CONTEXT}),
    AgentName.VERDICT_WRITER: frozenset(),
    AgentName.CONVERSATION: frozenset(
        {Capability.GET_CASE_CONTEXT, Capability.FIND_ENTITY_HISTORY}
    ),
}

INVESTIGATORS = (AgentName.TRIAGE, AgentName.PATTERNS)


def capabilities_of(agent: AgentName) -> frozenset[Capability]:
    return CAPABILITIES[agent]


def allows(agent: AgentName, capability: Capability) -> bool:
    return capability in CAPABILITIES[agent]
