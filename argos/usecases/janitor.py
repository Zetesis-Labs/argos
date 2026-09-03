"""Limpieza por referencias (constitución §10; S02 §12). Nunca se borra por
prefijo: se recorre el registro, se marca, y solo entonces se borra el objeto
exacto que ese registro nombra."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.model import Artifact, Document, Extraction
from argos.core.ports import LedgerConflictError, ObjectStoreError
from argos.core.retention import Sweep, plan_retention, plan_staging_sweep
from argos.usecases.deps import Services


@dataclass(frozen=True)
class SweepReport:
    swept: tuple[str, ...]
    removed: tuple[str, ...]
    skipped: tuple[str, ...]


async def _apply(services: Services, sweep: Sweep) -> tuple[str, ...] | None:
    """El objeto se borra antes de marcar la fila. Al revés, un borrado que falla
    dejaría el objeto huérfano: la fila marcada ya no vuelve a salir en el barrido."""
    if sweep.empty:
        return None
    removed: list[str] = []
    for key in sweep.keys:
        try:
            await services.object_store.delete(key)
        except ObjectStoreError:
            return None
        removed.append(key)
    try:
        await services.ledger.commit(sweep.ops)
    except LedgerConflictError:
        return None
    return tuple(removed)


async def sweep_staging(services: Services, *, limit: int = 100) -> SweepReport:
    """La subida interrumpida no llegó a ser trabajo: caduca y se borra su objeto."""
    now = services.clock.now()
    swept: list[str] = []
    removed: list[str] = []
    skipped: list[str] = []
    for artifact in await services.ledger.stale_artifacts(now, limit=limit):
        gone = await _apply(services, plan_staging_sweep(artifact, now=now))
        if gone is None:
            skipped.append(artifact.id)
            continue
        swept.append(artifact.id)
        removed.extend(gone)
    return SweepReport(swept=tuple(swept), removed=tuple(removed), skipped=tuple(skipped))


async def _artifacts_of(
    services: Services, document: Document, extractions: list[Extraction]
) -> list[Artifact]:
    identifiers = [document.artifact_id]
    for extraction in extractions:
        identifiers.extend((extraction.text_artifact_id, extraction.manifest_artifact_id))
    found: list[Artifact] = []
    for identifier in dict.fromkeys(identifiers):
        artifact = await services.ledger.artifact(identifier)
        if artifact is not None:
            found.append(artifact)
    return found


async def enforce_retention(services: Services, *, limit: int = 100) -> SweepReport:
    now = services.clock.now()
    swept: list[str] = []
    removed: list[str] = []
    skipped: list[str] = []
    for document in await services.ledger.expired_documents(now, limit=limit):
        extractions = await services.ledger.extractions_of_document(document.id)
        chunks = [
            chunk
            for extraction in extractions
            for chunk in await services.ledger.chunks(extraction.id)
        ]
        sweep = plan_retention(
            document=document,
            artifacts=await _artifacts_of(services, document, extractions),
            extractions=extractions,
            chunks=chunks,
            now=now,
        )
        gone = await _apply(services, sweep)
        if gone is None:
            skipped.append(document.id)
            continue
        swept.append(document.id)
        removed.extend(gone)
    return SweepReport(swept=tuple(swept), removed=tuple(removed), skipped=tuple(skipped))
