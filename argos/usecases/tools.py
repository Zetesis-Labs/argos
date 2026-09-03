"""Herramientas de negocio de los agentes (S02 §7). Solo lectura, por capacidad,
tenant y caso. Ninguna entrega SurrealQL general ni credenciales del almacén."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.agents import AgentName, Capability, allows
from argos.core.analysis import CaseAppearance, EntityHistory, aggregate_history
from argos.core.model import CaseState, EntityKind, ExtractionState, JobState, JobType
from argos.usecases.deps import Bookkeeping
from argos.usecases.queries import VerdictSummary, summary_of

NOT_AUTHORIZED = "tool.not_authorized"
CASE_NOT_FOUND = "case.not_found"
JOB_NOT_FOUND = "job.not_found"
EXTRACTION_NOT_FOUND = "extraction.not_found"


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
    document_ids: tuple[str, ...]
    extraction_ids: tuple[str, ...]
    verdict: VerdictSummary | None


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    type: JobType
    state: JobState
    attempt: int
    public_error: str | None
    document_id: str | None


@dataclass(frozen=True)
class PageRef:
    page: int
    chunks: int
    characters: int


@dataclass(frozen=True)
class ManifestView:
    extraction_id: str
    document_id: str
    page_count: int
    ocr_pages: int
    extractor_version: str
    pages: tuple[PageRef, ...]
    chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class ChunkView:
    chunk_id: str
    page: int
    position: int
    text: str


@dataclass(frozen=True)
class ChunkPage:
    extraction_id: str
    chunks: tuple[ChunkView, ...]
    cursor: int | None


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
    documents = await services.ledger.documents_of_case(case.id)
    extractions = [
        extraction
        for extraction in await services.ledger.extractions_of_case(case.id)
        if extraction.state is ExtractionState.AVAILABLE
    ]
    return CaseContext(
        case_id=case.id,
        state=case.state,
        language=case.language,
        document_ids=tuple(document.id for document in documents),
        extraction_ids=tuple(extraction.id for extraction in extractions),
        verdict=summary_of(await services.ledger.current_verdict(case.id)),
    )


async def get_document_job(
    services: Bookkeeping, caller: ToolCaller, *, job_id: str
) -> JobStatus | ToolDenied:
    denied = await _authorized_case(services, caller, Capability.GET_DOCUMENT_JOB)
    if denied is not None:
        return denied
    job = await services.ledger.job(job_id)
    if job is None or job.tenant_id != caller.tenant_id or job.case_id != caller.case_id:
        return ToolDenied(JOB_NOT_FOUND)
    return JobStatus(
        job_id=job.id,
        type=job.type,
        state=job.state,
        attempt=job.attempt,
        public_error=job.public_error,
        document_id=job.document_id,
    )


async def _authorized_extraction_chunks(
    services: Bookkeeping, caller: ToolCaller, capability: Capability, extraction_id: str
) -> ToolDenied | None:
    denied = await _authorized_case(services, caller, capability)
    if denied is not None:
        return denied
    extraction = await services.ledger.extraction(extraction_id)
    if (
        extraction is None
        or extraction.tenant_id != caller.tenant_id
        or extraction.case_id != caller.case_id
    ):
        return ToolDenied(EXTRACTION_NOT_FOUND)
    return None


async def get_extraction_manifest(
    services: Bookkeeping, caller: ToolCaller, *, extraction_id: str
) -> ManifestView | ToolDenied:
    denied = await _authorized_extraction_chunks(
        services, caller, Capability.GET_EXTRACTION_MANIFEST, extraction_id
    )
    if denied is not None:
        return denied
    extraction = await services.ledger.extraction(extraction_id)
    if extraction is None:
        return ToolDenied(EXTRACTION_NOT_FOUND)
    chunks = await services.ledger.chunks(extraction.id)
    pages = sorted({chunk.page for chunk in chunks})
    return ManifestView(
        extraction_id=extraction.id,
        document_id=extraction.document_id,
        page_count=extraction.page_count,
        ocr_pages=extraction.ocr_pages,
        extractor_version=extraction.extractor_version,
        pages=tuple(
            PageRef(
                page=page,
                chunks=sum(1 for chunk in chunks if chunk.page == page),
                characters=sum(len(chunk.text) for chunk in chunks if chunk.page == page),
            )
            for page in pages
        ),
        chunk_ids=tuple(chunk.id for chunk in chunks),
    )


async def get_extraction_chunks(
    services: Bookkeeping, caller: ToolCaller, *, extraction_id: str, cursor: int = 0
) -> ChunkPage | ToolDenied:
    denied = await _authorized_extraction_chunks(
        services, caller, Capability.GET_EXTRACTION_CHUNKS, extraction_id
    )
    if denied is not None:
        return denied
    budget = services.policy.analysis.chunk_budget
    chunks = [
        chunk for chunk in await services.ledger.chunks(extraction_id) if chunk.position >= cursor
    ]
    served = chunks[:budget]
    return ChunkPage(
        extraction_id=extraction_id,
        chunks=tuple(
            ChunkView(chunk_id=chunk.id, page=chunk.page, position=chunk.position, text=chunk.text)
            for chunk in served
        ),
        cursor=served[-1].position + 1 if len(chunks) > budget and served else None,
    )


async def find_registry_matches(
    services: Bookkeeping, caller: ToolCaller, *, kind: EntityKind, value: str
) -> tuple[RegistryMatch, ...] | ToolDenied:
    if not allows(caller.agent, Capability.FIND_REGISTRY_MATCHES):
        return ToolDenied(NOT_AUTHORIZED)
    warnings = await services.ledger.warnings_for(kind, value)
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
    entity = await services.ledger.entity_by_value(kind, value)
    if entity is None:
        return aggregate_history(kind, value, ())
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
