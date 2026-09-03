"""Catálogo de agentes y sus capacidades (constitución §8; S02 §4, §7).

Todas las capacidades son de lectura: crear, reprocesar o cerrar un trabajo es
un caso de uso del gateway o del curador, nunca una herramienta de un agente.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class AgentName(StrEnum):
    TRIAGE = "triage_agent"
    REGISTRIES = "registries_agent"
    DOMAIN = "domain_agent"
    PATTERNS = "patterns_agent"
    MEMORY = "memory_agent"
    DOCUMENT = "document_agent"
    VERDICT_WRITER = "verdict_writer"
    CONVERSATION = "conversation_agent"


class Capability(StrEnum):
    GET_CASE_CONTEXT = "get_case_context"
    FIND_REGISTRY_MATCHES = "find_registry_matches"
    FIND_ENTITY_HISTORY = "find_entity_history"
    GET_DOCUMENT_JOB = "get_document_job"
    GET_EXTRACTION_MANIFEST = "get_extraction_manifest"
    GET_EXTRACTION_CHUNKS = "get_extraction_chunks"


CAPABILITIES: Mapping[AgentName, frozenset[Capability]] = {
    AgentName.TRIAGE: frozenset({Capability.GET_CASE_CONTEXT, Capability.GET_EXTRACTION_CHUNKS}),
    AgentName.REGISTRIES: frozenset({Capability.FIND_REGISTRY_MATCHES}),
    AgentName.DOMAIN: frozenset({Capability.GET_CASE_CONTEXT}),
    AgentName.PATTERNS: frozenset({Capability.GET_CASE_CONTEXT, Capability.GET_EXTRACTION_CHUNKS}),
    AgentName.MEMORY: frozenset({Capability.FIND_ENTITY_HISTORY}),
    AgentName.DOCUMENT: frozenset(
        {
            Capability.GET_DOCUMENT_JOB,
            Capability.GET_EXTRACTION_MANIFEST,
            Capability.GET_EXTRACTION_CHUNKS,
        }
    ),
    AgentName.VERDICT_WRITER: frozenset(),
    AgentName.CONVERSATION: frozenset(
        {Capability.GET_CASE_CONTEXT, Capability.FIND_ENTITY_HISTORY}
    ),
}

INVESTIGATION_TEAM = (
    AgentName.TRIAGE,
    AgentName.REGISTRIES,
    AgentName.DOMAIN,
    AgentName.PATTERNS,
    AgentName.MEMORY,
)


def capabilities_of(agent: AgentName) -> frozenset[Capability]:
    return CAPABILITIES[agent]


def allows(agent: AgentName, capability: Capability) -> bool:
    return capability in CAPABILITIES[agent]
