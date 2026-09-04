"""Activación de un bundle validado sobre una proyección local."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from argos.core.knowledge import (
    PROJECTION_VERSION,
    KnowledgeSnapshot,
    parse_knowledge_bundle,
    warnings_from_bundle,
)
from argos.core.ports import KnowledgeProjection


@dataclass(frozen=True)
class KnowledgeImportReport:
    changed: bool
    source_head: str
    content_hash: str
    nodes: int
    edges: int
    warnings: int


async def activate_knowledge(
    content: bytes, projection: KnowledgeProjection, *, imported_at: datetime
) -> KnowledgeImportReport:
    bundle = parse_knowledge_bundle(content)
    warnings = warnings_from_bundle(bundle)
    snapshot = KnowledgeSnapshot(
        source_head=bundle.source_head,
        content_hash=bundle.content_hash,
        graph_schema=bundle.schema,
        profile=bundle.profile,
        projection_version=PROJECTION_VERSION,
        imported_at=imported_at,
        node_count=len(bundle.nodes),
        edge_count=len(bundle.edges),
        warning_count=len(warnings),
    )
    changed = await projection.activate(snapshot, bundle, warnings)
    return KnowledgeImportReport(
        changed=changed,
        source_head=bundle.source_head,
        content_hash=bundle.content_hash,
        nodes=len(bundle.nodes),
        edges=len(bundle.edges),
        warnings=len(warnings),
    )
