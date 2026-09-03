"""Herramientas que recibe cada agente: las de su capacidad y ninguna más (S02 §7).

Devuelven JSON determinista. Ninguna acepta SurrealQL ni entrega claves del almacén.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from argos.core.agents import AgentName, Capability, allows
from argos.core.analysis import EntityHistory
from argos.core.model import EntityKind
from argos.usecases import tools as ops
from argos.usecases.deps import Bookkeeping
from argos.usecases.tools import (
    CaseContext,
    ChunkPage,
    JobStatus,
    ManifestView,
    RegistryMatch,
    ToolCaller,
    ToolDenied,
)

type NoInput = Callable[[], Awaitable[str]]
type OneInput = Callable[[str], Awaitable[str]]
type TwoInputs = Callable[[str, str], Awaitable[str]]
type TextAndNumber = Callable[[str, int], Awaitable[str]]
type AgentTool = NoInput | OneInput | TwoInputs | TextAndNumber

UNKNOWN_KIND = "entity.unknown_kind"


@dataclass(frozen=True)
class BoundTool:
    capability: Capability
    call: AgentTool


def dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def denial(denied: ToolDenied) -> str:
    return dumps({"error": denied.code})


def case_context_payload(context: CaseContext) -> dict[str, object]:
    verdict = context.verdict
    return {
        "case_id": context.case_id,
        "state": str(context.state),
        "language": context.language,
        "document_ids": list(context.document_ids),
        "extraction_ids": list(context.extraction_ids),
        "verdict": None
        if verdict is None
        else {
            "version": verdict.version,
            "level": str(verdict.level),
            "outcome": str(verdict.outcome),
            "summary": verdict.summary,
            "actions": list(verdict.actions),
            "missing": list(verdict.missing),
        },
    }


def job_payload(status: JobStatus) -> dict[str, object]:
    return {
        "job_id": status.job_id,
        "type": str(status.type),
        "state": str(status.state),
        "attempt": status.attempt,
        "public_error": status.public_error,
        "document_id": status.document_id,
    }


def manifest_payload(manifest: ManifestView) -> dict[str, object]:
    return {
        "extraction_id": manifest.extraction_id,
        "document_id": manifest.document_id,
        "page_count": manifest.page_count,
        "ocr_pages": manifest.ocr_pages,
        "extractor_version": manifest.extractor_version,
        "pages": [
            {"page": page.page, "chunks": page.chunks, "characters": page.characters}
            for page in manifest.pages
        ],
        "chunk_ids": list(manifest.chunk_ids),
    }


def chunks_payload(page: ChunkPage) -> dict[str, object]:
    return {
        "extraction_id": page.extraction_id,
        "cursor": page.cursor,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "page": chunk.page,
                "position": chunk.position,
                "text": chunk.text,
            }
            for chunk in page.chunks
        ],
    }


def matches_payload(matches: Sequence[RegistryMatch]) -> dict[str, object]:
    return {
        "matches": [
            {
                "regulator": match.regulator,
                "url": match.url,
                "captured_at": match.captured_at,
                "active": match.active,
            }
            for match in matches
        ]
    }


def history_payload(history: EntityHistory) -> dict[str, object]:
    return {
        "kind": str(history.kind),
        "value": history.value,
        "cases": history.cases,
        "first_seen_at": None
        if history.first_seen_at is None
        else history.first_seen_at.isoformat(),
        "last_seen_at": None if history.last_seen_at is None else history.last_seen_at.isoformat(),
        "confirmed": history.confirmed,
    }


def _kind_of(raw: str) -> EntityKind | None:
    try:
        return EntityKind(raw)
    except ValueError:
        return None


def tools_for(services: Bookkeeping, caller: ToolCaller) -> list[BoundTool]:
    async def get_case_context() -> str:
        """Devuelve el estado del caso, sus documentos, extracciones y veredicto vigente."""
        result = await ops.get_case_context(services, caller)
        if isinstance(result, ToolDenied):
            return denial(result)
        return dumps(case_context_payload(result))

    async def get_document_job(job_id: str) -> str:
        """Devuelve el estado público de un trabajo de documento del caso."""
        result = await ops.get_document_job(services, caller, job_id=job_id)
        if isinstance(result, ToolDenied):
            return denial(result)
        return dumps(job_payload(result))

    async def get_extraction_manifest(extraction_id: str) -> str:
        """Devuelve la estructura de una extracción: páginas, tamaños y fragmentos."""
        result = await ops.get_extraction_manifest(services, caller, extraction_id=extraction_id)
        if isinstance(result, ToolDenied):
            return denial(result)
        return dumps(manifest_payload(result))

    async def get_extraction_chunks(extraction_id: str, cursor: int = 0) -> str:
        """Devuelve una página de fragmentos autorizados de la extracción."""
        result = await ops.get_extraction_chunks(
            services, caller, extraction_id=extraction_id, cursor=cursor
        )
        if isinstance(result, ToolDenied):
            return denial(result)
        return dumps(chunks_payload(result))

    async def find_registry_matches(kind: str, value: str) -> str:
        """Busca advertencias oficiales vigentes sobre un identificador."""
        entity_kind = _kind_of(kind)
        if entity_kind is None:
            return dumps({"error": UNKNOWN_KIND})
        result = await ops.find_registry_matches(services, caller, kind=entity_kind, value=value)
        if isinstance(result, ToolDenied):
            return denial(result)
        return dumps(matches_payload(result))

    async def find_entity_history(kind: str, value: str) -> str:
        """Devuelve los agregados de reincidencia de un identificador, sin casos ajenos."""
        entity_kind = _kind_of(kind)
        if entity_kind is None:
            return dumps({"error": UNKNOWN_KIND})
        result = await ops.find_entity_history(services, caller, kind=entity_kind, value=value)
        if isinstance(result, ToolDenied):
            return denial(result)
        return dumps(history_payload(result))

    catalog: tuple[BoundTool, ...] = (
        BoundTool(Capability.GET_CASE_CONTEXT, get_case_context),
        BoundTool(Capability.GET_DOCUMENT_JOB, get_document_job),
        BoundTool(Capability.GET_EXTRACTION_MANIFEST, get_extraction_manifest),
        BoundTool(Capability.GET_EXTRACTION_CHUNKS, get_extraction_chunks),
        BoundTool(Capability.FIND_REGISTRY_MATCHES, find_registry_matches),
        BoundTool(Capability.FIND_ENTITY_HISTORY, find_entity_history),
    )
    return [bound for bound in catalog if allows(caller.agent, bound.capability)]


def tool_names(agent: AgentName) -> frozenset[str]:
    return frozenset(str(capability) for capability in Capability if allows(agent, capability))
