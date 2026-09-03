"""W5 · Extracción de un documento: el trabajo pesado y determinista del worker (S02 §9)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import zstandard

from argos.core.extraction import (
    PageText,
    TextSource,
    build_chunks,
    full_text,
    is_usable,
    manifest,
    normalize,
    ocr_pages,
)
from argos.core.keys import extraction_manifest_key, extraction_text_key
from argos.core.model import Attempt, FailureKind, Job
from argos.core.observability import (
    Attributes,
    attempt_attributes,
    extraction_attributes,
    job_attributes,
)
from argos.core.ports import (
    ObjectStoreError,
    OpenPdf,
    PageOcr,
    PdfError,
    PdfReader,
    PdfTooManyPagesError,
    StoredObject,
)
from argos.platform.spans import annotate, span
from argos.usecases.consumers import (
    ExtractedChunk,
    ExtractionResult,
    Skipped,
    complete_extraction,
    fail_attempt,
)
from argos.usecases.deps import Services

HASH_MISMATCH = "document.hash_mismatch"
OBJECT_MISSING = "document.object_missing"
TEXT_MIME = "application/zstd"
MANIFEST_MIME = "application/json"


@dataclass(frozen=True)
class PdfTools:
    reader: PdfReader
    ocr: PageOcr


async def one_shot(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


def read_pages(document: OpenPdf, tools: PdfTools, *, services: Services) -> tuple[PageText, ...]:
    policy = services.policy.extraction
    pages: list[PageText] = []
    for number in range(1, document.page_count + 1):
        embedded = document.text_of(number)
        if is_usable(embedded, min_chars=policy.min_usable_chars_per_page):
            pages.append(
                PageText(number=number, text=normalize(embedded), source=TextSource.EMBEDDED)
            )
            continue
        image = document.image_of(number, scale=policy.render_scale)
        recognized = tools.ocr.text_of(image, language=policy.ocr_language)
        pages.append(PageText(number=number, text=normalize(recognized), source=TextSource.OCR))
    return tuple(pages)


async def extract_document(
    services: Services, tools: PdfTools, *, job: Job, attempt: Attempt
) -> Job | Skipped:
    with span("argos.extract", job_attributes(job) | attempt_attributes(attempt)) as current:

        def record(attributes: Attributes) -> None:
            annotate(current, attributes)

        return await _extract(services, tools, job=job, attempt=attempt, record=record)


async def _extract(
    services: Services,
    tools: PdfTools,
    *,
    job: Job,
    attempt: Attempt,
    record: Callable[[Attributes], None],
) -> Job | Skipped:
    ledger = services.ledger
    if job.document_id is None:
        return Skipped("the job carries no document")
    document = await ledger.document(job.document_id)
    if document is None:
        return Skipped("unknown document")
    artifact = await ledger.artifact(document.artifact_id)
    if artifact is None:
        return Skipped("unknown artifact")

    payload = await services.object_store.read(
        artifact.key, limit=services.policy.documents.max_bytes
    )
    if payload is None:
        return await _fail(services, job, attempt, FailureKind.PERMANENT, OBJECT_MISSING)
    digest = _sha256(payload)
    if digest != document.sha256 or len(payload) != document.size:
        return await _fail(services, job, attempt, FailureKind.PERMANENT, HASH_MISMATCH)

    try:
        pages = _pages_of(payload, tools, services=services)
    except PdfError as error:
        return await _fail(services, job, attempt, FailureKind.PERMANENT, error.code)

    text = full_text(pages)
    chunks = build_chunks(pages, max_chars=services.policy.extraction.chunk_max_chars)
    record(
        extraction_attributes(
            pages=len(pages),
            ocr_pages=ocr_pages(pages),
            chunks=len(chunks),
            bytes_read=len(payload),
        )
    )
    compressed = zstandard.ZstdCompressor().compress(text.encode())
    described = manifest(
        pages,
        chunks,
        extractor_version=job.extractor_version,
        options=job.options,
        text_sha256=_sha256(text.encode()),
    )
    extraction_id = services.ids.new_id()
    try:
        stored_text, stored_manifest = await _store_derivatives(
            services, job, extraction_id, compressed=compressed, described=described
        )
    except ObjectStoreError:
        return await _fail(services, job, attempt, FailureKind.TRANSIENT, "store.unavailable")

    return await complete_extraction(
        services,
        job_id=job.id,
        attempt_number=attempt.number,
        result=ExtractionResult(
            extraction_id=extraction_id,
            text_object=stored_text,
            manifest_object=stored_manifest,
            sha256=_sha256(text.encode()),
            page_count=len(pages),
            ocr_pages=ocr_pages(pages),
            chunks=tuple(
                ExtractedChunk(
                    page=chunk.page, position=chunk.position, text=chunk.text, sha256=chunk.sha256
                )
                for chunk in chunks
            ),
        ),
    )


def _pages_of(payload: bytes, tools: PdfTools, *, services: Services) -> tuple[PageText, ...]:
    document = tools.reader.open(payload)
    try:
        if document.page_count > services.policy.documents.max_pages:
            raise PdfTooManyPagesError(
                f"{document.page_count} pages exceed {services.policy.documents.max_pages}"
            )
        return read_pages(document, tools, services=services)
    finally:
        document.close()


async def _store_derivatives(
    services: Services, job: Job, extraction_id: str, *, compressed: bytes, described: bytes
) -> tuple[StoredObject, StoredObject]:
    extraction_key = extraction_text_key(job.tenant_id, job.case_id, extraction_id)
    manifest_key = extraction_manifest_key(job.tenant_id, job.case_id, extraction_id)
    stored_text = await services.object_store.put(
        extraction_key, one_shot(compressed), size=len(compressed), mime=TEXT_MIME
    )
    stored_manifest = await services.object_store.put(
        manifest_key, one_shot(described), size=len(described), mime=MANIFEST_MIME
    )
    return stored_text, stored_manifest


async def _fail(
    services: Services, job: Job, attempt: Attempt, kind: FailureKind, code: str
) -> Job | Skipped:
    return await fail_attempt(
        services, job_id=job.id, attempt_number=attempt.number, kind=kind, code=code
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
