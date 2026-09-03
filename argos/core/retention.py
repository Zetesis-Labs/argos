"""Retención y limpieza (constitución §6, §12). Se marca, se comprueban las
referencias y se borra el objeto exacto; nunca por prefijo ni por estado local."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from argos.core.model import (
    Artifact,
    ArtifactState,
    Chunk,
    Delete,
    Document,
    DocumentState,
    Extraction,
    ExtractionState,
    LedgerOp,
    Update,
)


@dataclass(frozen=True)
class Sweep:
    """Lo que hay que escribir y qué objetos borrar después de escribirlo."""

    ops: tuple[LedgerOp, ...]
    keys: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not self.ops and not self.keys


def plan_staging_sweep(artifact: Artifact, *, now: datetime) -> Sweep:
    if artifact.state is not ArtifactState.UPLOADING or artifact.expires_at > now:
        return Sweep(ops=(), keys=())
    return Sweep(ops=(Update(deleted(artifact)),), keys=(artifact.key,))


def deleted(artifact: Artifact) -> Artifact:
    return replace(
        artifact,
        state=ArtifactState.DELETED,
        sha256=None,
        size=0,
        revision=artifact.revision + 1,
    )


def plan_retention(
    *,
    document: Document,
    artifacts: Sequence[Artifact],
    extractions: Sequence[Extraction],
    chunks: Sequence[Chunk],
    now: datetime,
) -> Sweep:
    """El documento caducado se lleva sus derivados; el caso, sus señales y su
    veredicto no se tocan (R8)."""
    if document.state is DocumentState.EXPIRED or document.expires_at > now:
        return Sweep(ops=(), keys=())
    ops: list[LedgerOp] = [Delete(chunk) for chunk in chunks]
    ops.extend(
        Update(replace(extraction, state=ExtractionState.EXPIRED, revision=extraction.revision + 1))
        for extraction in extractions
        if extraction.state is not ExtractionState.EXPIRED
    )
    live = [artifact for artifact in artifacts if artifact.state is not ArtifactState.DELETED]
    ops.extend(Update(deleted(artifact)) for artifact in live)
    ops.append(
        Update(replace(document, state=DocumentState.EXPIRED, revision=document.revision + 1))
    )
    return Sweep(ops=tuple(ops), keys=tuple(artifact.key for artifact in live))
