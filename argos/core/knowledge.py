"""Contrato puro del bundle de conocimiento que Argos acepta para proyectar."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from argos.core.analysis import normalized_identifier
from argos.core.model import EntityKind, OfficialWarning

GRAPH_SCHEMA = "okf-graph/v1"
PROFILE_ID = "https://argos.local/okf/profiles/knowledge/v1"
PROJECTION_VERSION = 1
OPERATIONAL_TYPES = frozenset({"entity", "warning", "regulator", "source"})
OPERATIONAL_EDGES = frozenset({"Warns about", "Issued by", "Cites"})
WARNING_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
KNOWLEDGE_ID = re.compile(r"[a-z][a-z0-9+.-]*:[^\s]+")
GIT_HEAD = re.compile(r"[0-9a-f]{40}")
SOURCE_KINDS = frozenset(
    {"official-warning", "official-register", "official-guidance"}
)
ENTITY_STRENGTHS = frozenset({"strong", "weak"})


class KnowledgeError(ValueError):
    pass


@dataclass(frozen=True)
class KnowledgeNode:
    knowledge_id: str
    slug: str
    title: str
    kind: str
    description: str | None
    path: str | None
    properties: dict[str, object]


@dataclass(frozen=True)
class KnowledgeEdge:
    source: str
    target: str
    label: str
    derived: bool


@dataclass(frozen=True)
class KnowledgeBundle:
    schema: str
    profile: str
    source_head: str
    content_hash: str
    types: frozenset[str]
    edge_labels: frozenset[str]
    nodes: tuple[KnowledgeNode, ...]
    edges: tuple[KnowledgeEdge, ...]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeSnapshot:
    source_head: str
    content_hash: str
    graph_schema: str
    profile: str
    projection_version: int
    imported_at: datetime
    node_count: int
    edge_count: int
    warning_count: int


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise KnowledgeError(f"{label}: se esperaba un objeto")
    return cast(dict[str, object], value)


def _items(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise KnowledgeError(f"{label}: se esperaba una lista")
    return cast(list[object], value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeError(f"{label}: se esperaba texto")
    return value.strip()


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _string_set(value: object, label: str) -> frozenset[str]:
    values = _items(value, label)
    if not all(isinstance(item, str) for item in values):
        raise KnowledgeError(f"{label}: todos los valores deben ser texto")
    return frozenset(cast(list[str], values))


def _parse_node(value: object, position: int) -> KnowledgeNode:
    raw = _mapping(value, f"nodo {position}")
    properties = _mapping(raw.get("properties", {}), f"nodo {position}.properties")
    knowledge_id = _text(
        properties.get("knowledge_id"), f"nodo {position}.properties.knowledge_id"
    )
    if KNOWLEDGE_ID.fullmatch(knowledge_id) is None:
        raise KnowledgeError(
            f"nodo {position}.properties.knowledge_id: debe ser una URI estable"
        )
    return KnowledgeNode(
        knowledge_id=knowledge_id,
        slug=_text(raw.get("slug"), f"nodo {position}.slug"),
        title=_text(raw.get("title"), f"nodo {position}.title"),
        kind=_text(raw.get("type"), f"nodo {position}.type"),
        description=_optional_text(raw.get("description"), f"nodo {position}.description"),
        path=_optional_text(raw.get("path"), f"nodo {position}.path"),
        properties=properties,
    )


def _parse_edge(value: object, position: int) -> KnowledgeEdge:
    raw = _mapping(value, f"relación {position}")
    derived = raw.get("derived", False)
    if not isinstance(derived, bool):
        raise KnowledgeError(f"relación {position}.derived: se esperaba booleano")
    return KnowledgeEdge(
        source=_text(raw.get("source"), f"relación {position}.source"),
        target=_text(raw.get("target"), f"relación {position}.target"),
        label=_text(raw.get("label"), f"relación {position}.label"),
        derived=derived,
    )


def parse_knowledge_bundle(content: bytes) -> KnowledgeBundle:
    try:
        decoded = cast(object, json.loads(content))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KnowledgeError("bundle: JSON inválido") from error
    raw = _mapping(decoded, "bundle")
    schema = _text(raw.get("schema"), "bundle.schema")
    if schema != GRAPH_SCHEMA:
        raise KnowledgeError(f"bundle.schema: debe ser {GRAPH_SCHEMA}")
    profile = _text(raw.get("okf_profile"), "bundle.okf_profile")
    if profile != PROFILE_ID:
        raise KnowledgeError(f"bundle.okf_profile: debe ser {PROFILE_ID}")
    source_head = _text(raw.get("source_head"), "bundle.source_head")
    if GIT_HEAD.fullmatch(source_head) is None:
        raise KnowledgeError("bundle.source_head: debe ser un commit Git completo")
    types = _string_set(raw.get("types"), "bundle.types")
    if not types >= OPERATIONAL_TYPES:
        missing = sorted(OPERATIONAL_TYPES - types)
        raise KnowledgeError(f"bundle.types: faltan tipos operacionales {missing}")
    edge_labels = _string_set(raw.get("edgeLabels"), "bundle.edgeLabels")
    if not edge_labels >= OPERATIONAL_EDGES:
        missing = sorted(OPERATIONAL_EDGES - edge_labels)
        raise KnowledgeError(f"bundle.edgeLabels: faltan relaciones operacionales {missing}")

    unresolved_items = _items(raw.get("unresolved"), "bundle.unresolved")
    if unresolved_items:
        raise KnowledgeError("bundle: contiene relaciones sin resolver")

    nodes = tuple(
        _parse_node(item, position)
        for position, item in enumerate(_items(raw.get("nodes"), "bundle.nodes"), start=1)
    )
    slugs = [node.slug for node in nodes]
    if len(slugs) != len(set(slugs)):
        raise KnowledgeError("bundle.nodes: slug duplicado")
    unknown_types = sorted({node.kind for node in nodes} - types)
    if unknown_types:
        raise KnowledgeError(f"bundle.nodes: tipos desconocidos {unknown_types}")
    knowledge_ids = [node.knowledge_id for node in nodes]
    if len(knowledge_ids) != len(set(knowledge_ids)):
        raise KnowledgeError("bundle.nodes: knowledge_id duplicado")
    warning_ids: set[str] = set()
    for node in nodes:
        _validate_node(node)
        if node.kind == "warning":
            warning_id = _text(
                _property(node, "warning_id"), f"{node.slug}.warning_id"
            )
            if warning_id in warning_ids:
                raise KnowledgeError(f"{node.slug}: warning_id duplicado {warning_id}")
            warning_ids.add(warning_id)

    edges = tuple(
        _parse_edge(item, position)
        for position, item in enumerate(_items(raw.get("edges"), "bundle.edges"), start=1)
    )
    known = set(slugs)
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in edges:
        if edge.label not in edge_labels:
            raise KnowledgeError(f"relación {edge.label}: etiqueta desconocida")
        if edge.source not in known or edge.target not in known:
            raise KnowledgeError(f"relación {edge.label}: origen o destino sin resolver")
        key = (edge.source, edge.label, edge.target)
        if key in seen_edges:
            raise KnowledgeError(f"relación {edge.label}: duplicada")
        seen_edges.add(key)

    return KnowledgeBundle(
        schema=schema,
        profile=profile,
        source_head=source_head,
        content_hash=hashlib.sha256(content).hexdigest(),
        types=types,
        edge_labels=edge_labels,
        nodes=nodes,
        edges=edges,
        unresolved=(),
    )


def _property(node: KnowledgeNode, name: str) -> object:
    value = node.properties.get(name)
    if value is None or value == "":
        raise KnowledgeError(f"{node.slug}: falta la propiedad {name}")
    return value


def _captured_at(node: KnowledgeNode) -> datetime:
    captured_raw = _text(_property(node, "captured_at"), f"{node.slug}.captured_at")
    try:
        captured_at = datetime.fromisoformat(captured_raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise KnowledgeError(f"{node.slug}: captured_at no es ISO 8601") from error
    if captured_at.tzinfo is None:
        raise KnowledgeError(f"{node.slug}: captured_at necesita zona horaria")
    return captured_at.astimezone(UTC)


def _validate_node(node: KnowledgeNode) -> None:
    if node.kind == "entity":
        raw_kind = _text(_property(node, "entity_kind"), f"{node.slug}.entity_kind")
        try:
            entity_kind = EntityKind(raw_kind)
        except ValueError as error:
            raise KnowledgeError(f"{node.slug}: entity_kind desconocido") from error
        normalized_identifier(
            entity_kind,
            _text(_property(node, "entity_value"), f"{node.slug}.entity_value"),
        )
        strength = _text(_property(node, "strength"), f"{node.slug}.strength")
        if strength not in ENTITY_STRENGTHS:
            raise KnowledgeError(f"{node.slug}: strength debe ser strong o weak")
        return
    if node.kind == "warning":
        warning_id = _text(_property(node, "warning_id"), f"{node.slug}.warning_id")
        if WARNING_ID.fullmatch(warning_id) is None:
            raise KnowledgeError(f"{node.slug}: warning_id no es estable")
        status = _text(_property(node, "status"), f"{node.slug}.status")
        if status not in {"active", "withdrawn"}:
            raise KnowledgeError(f"{node.slug}: status debe ser active o withdrawn")
        _captured_at(node)
        return
    if node.kind == "regulator":
        _text(_property(node, "code"), f"{node.slug}.code")
        return
    if node.kind == "source":
        url = _text(_property(node, "url"), f"{node.slug}.url")
        if not url.startswith("https://"):
            raise KnowledgeError(f"{node.slug}: url debe usar HTTPS")
        source_kind = _text(
            _property(node, "source_kind"), f"{node.slug}.source_kind"
        )
        if source_kind not in SOURCE_KINDS:
            raise KnowledgeError(f"{node.slug}: source_kind desconocido")


def _single_target(
    warning: KnowledgeNode,
    edges: tuple[KnowledgeEdge, ...],
    nodes: dict[str, KnowledgeNode],
    label: str,
    kind: str,
) -> KnowledgeNode:
    targets = [
        nodes[edge.target]
        for edge in edges
        if not edge.derived and edge.source == warning.slug and edge.label == label
    ]
    if len(targets) != 1 or targets[0].kind != kind:
        raise KnowledgeError(f"{warning.slug}: necesita exactamente un {label} hacia {kind}")
    return targets[0]


def warnings_from_bundle(bundle: KnowledgeBundle) -> tuple[OfficialWarning, ...]:
    nodes = {node.slug: node for node in bundle.nodes}
    warnings: list[OfficialWarning] = []
    identifiers: set[str] = set()
    for node in bundle.nodes:
        if node.kind != "warning":
            continue
        entity = _single_target(node, bundle.edges, nodes, "Warns about", "entity")
        regulator = _single_target(node, bundle.edges, nodes, "Issued by", "regulator")
        source = _single_target(node, bundle.edges, nodes, "Cites", "source")

        warning_id = _text(_property(node, "warning_id"), f"{node.slug}.warning_id")
        if WARNING_ID.fullmatch(warning_id) is None:
            raise KnowledgeError(f"{node.slug}: warning_id no es estable")
        if warning_id in identifiers:
            raise KnowledgeError(f"{node.slug}: warning_id duplicado {warning_id}")
        identifiers.add(warning_id)

        status = _text(_property(node, "status"), f"{node.slug}.status")
        if status not in {"active", "withdrawn"}:
            raise KnowledgeError(f"{node.slug}: status debe ser active o withdrawn")
        captured_at = _captured_at(node)

        raw_kind = _text(_property(entity, "entity_kind"), f"{entity.slug}.entity_kind")
        try:
            entity_kind = EntityKind(raw_kind)
        except ValueError as error:
            raise KnowledgeError(f"{entity.slug}: entity_kind desconocido") from error
        entity_value = normalized_identifier(
            entity_kind,
            _text(_property(entity, "entity_value"), f"{entity.slug}.entity_value"),
        )
        url = _text(_property(source, "url"), f"{source.slug}.url")
        if not url.startswith("https://"):
            raise KnowledgeError(f"{source.slug}: url debe usar HTTPS")
        regulator_code = _text(
            _property(regulator, "code"), f"{regulator.slug}.code"
        )
        warnings.append(
            OfficialWarning(
                id=warning_id,
                regulator=regulator_code,
                url=url,
                entity_kind=entity_kind,
                entity_value=entity_value,
                active=status == "active",
                captured_at=captured_at,
                revision=0,
            )
        )
    return tuple(warnings)
