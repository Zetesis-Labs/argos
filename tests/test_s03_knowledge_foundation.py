import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from argos.config import Settings
from argos.core.knowledge import (
    PROJECTION_VERSION,
    KnowledgeError,
    KnowledgeSnapshot,
    parse_knowledge_bundle,
    warnings_from_bundle,
)
from argos.core.model import EntityKind
from argos.core.ports import LedgerError
from argos.devtools.bootstrap_db import apply_schema
from argos.devtools.project_knowledge import project_knowledge
from argos.platform.knowledge import SurrealKnowledgeProjection
from argos.platform.ledger import ledger_for
from argos.tools.fakes import InMemoryKnowledgeProjection
from argos.usecases.knowledge import activate_knowledge

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


def bundle_bytes(*, source_head: str = "a" * 40, warning_status: str = "active") -> bytes:
    graph: dict[str, object] = {
        "schema": "okf-graph/v1",
        "okf_version": "0.1",
        "okf_profile": "https://argos.local/okf/profiles/knowledge/v1",
        "source_head": source_head,
        "stale": False,
        "types": [
            "entity",
            "warning",
            "regulator",
            "source",
            "jurisdiction",
            "typology",
            "pattern",
            "guidance",
        ],
        "edgeLabels": [
            "Warns about",
            "Issued by",
            "Cites",
            "Operates in",
            "Same as",
            "Supersedes",
            "Part of",
            "Contains",
        ],
        "propertyGroups": [],
        "stats": {
            "notes": 4,
            "edges": 3,
            "declaredEdges": 3,
            "derivedEdges": 0,
            "unresolvedEdges": 0,
        },
        "nodes": [
            {
                "slug": "entities/example-broker",
                "title": "Example Broker",
                "type": "entity",
                "path": "entities/example-broker.md",
                "properties": {
                    "knowledge_id": "urn:argos:entity:example-broker",
                    "entity_kind": "domain",
                    "entity_value": "Example-Broker.test",
                    "strength": "strong",
                },
            },
            {
                "slug": "regulators/fca",
                "title": "FCA",
                "type": "regulator",
                "path": "regulators/fca.md",
                "properties": {
                    "knowledge_id": "urn:argos:regulator:fca",
                    "code": "FCA",
                },
            },
            {
                "slug": "sources/fca-example-broker",
                "title": "FCA synthetic warning",
                "type": "source",
                "path": "sources/fca-example-broker.md",
                "properties": {
                    "knowledge_id": "urn:argos:source:fca-example-broker",
                    "url": "https://warnings.fca.example/demo/example-broker",
                    "source_kind": "official-warning",
                },
            },
            {
                "slug": "warnings/fca-example-broker",
                "title": "Warning about Example Broker",
                "type": "warning",
                "path": "warnings/fca-example-broker.md",
                "properties": {
                    "knowledge_id": "urn:argos:warning:fca-example-broker",
                    "warning_id": "demo-fca-example-broker",
                    "status": warning_status,
                    "captured_at": "2026-09-01T00:00:00Z",
                },
            },
        ],
        "edges": [
            {
                "source": "warnings/fca-example-broker",
                "target": "entities/example-broker",
                "label": "Warns about",
            },
            {
                "source": "warnings/fca-example-broker",
                "target": "regulators/fca",
                "label": "Issued by",
            },
            {
                "source": "warnings/fca-example-broker",
                "target": "sources/fca-example-broker",
                "label": "Cites",
            },
        ],
        "unresolved": [],
    }
    return json.dumps(graph, separators=(",", ":")).encode()


async def test_corpus_and_profile_build_a_closed_okf_graph(settings: Settings) -> None:
    """S03.1 el corpus y el perfil producen un grafo OKF cerrado."""
    bundle = parse_knowledge_bundle(settings.knowledge_graph_path.read_bytes())

    assert bundle.source_head
    assert {node.kind for node in bundle.nodes} == {
        "entity",
        "warning",
        "regulator",
        "source",
    }
    assert len(bundle.nodes) == len({node.slug for node in bundle.nodes})
    assert len(bundle.nodes) == len({node.knowledge_id for node in bundle.nodes})
    assert all(node.knowledge_id.startswith("urn:argos:") for node in bundle.nodes)
    assert not bundle.unresolved


def test_explorer_declares_context_and_question_modes() -> None:
    """S03.2 el explorador expresa preguntas sin cambiar los hechos."""
    config = Path("okf.config.mjs").read_text(encoding="utf-8")
    compose = Path(".devcontainer/docker-compose.yml").read_text(encoding="utf-8")

    assert "injectAccess: true" in config
    assert set(re.findall(r'id: "(risk|identity|provenance|history)"', config)) == {
        "risk",
        "identity",
        "provenance",
        "history",
    }
    assert '"127.0.0.1:${KNOWLEDGE_PORT:-8400}:8080"' in compose
    for label in ("Vigente", "Retirada", "Entidad", "Advertencia", "Regulador", "Fuente"):
        assert label in config


def _decoded_bundle() -> dict[str, object]:
    decoded = cast(object, json.loads(bundle_bytes()))
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


def _object_list(graph: dict[str, object], key: str) -> list[object]:
    value = graph[key]
    assert isinstance(value, list)
    return cast(list[object], value)


def _object_at(values: list[object], position: int) -> dict[str, object]:
    value = values[position]
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _mutate_invalid_bundle(graph: dict[str, object], case: str) -> None:
    if case == "wrong-schema":
        graph["schema"] = "other/v1"
        return
    if case == "duplicate-slug":
        nodes = _object_list(graph, "nodes")
        nodes.append(nodes[0])
        return
    if case == "missing-knowledge-id":
        node = _object_at(_object_list(graph, "nodes"), 0)
        properties = node["properties"]
        assert isinstance(properties, dict)
        cast(dict[str, object], properties).pop("knowledge_id")
        return
    if case == "duplicate-knowledge-id":
        nodes = _object_list(graph, "nodes")
        first = _object_at(nodes, 0)
        second = _object_at(nodes, 1)
        first_properties = first["properties"]
        second_properties = second["properties"]
        assert isinstance(first_properties, dict) and isinstance(second_properties, dict)
        cast(dict[str, object], second_properties)["knowledge_id"] = cast(
            dict[str, object], first_properties
        )["knowledge_id"]
        return
    if case == "unresolved-edge":
        _object_list(graph, "unresolved").append({"target": "missing"})
        return
    if case == "bad-source-kind":
        source = _object_at(_object_list(graph, "nodes"), 2)
        properties = source["properties"]
        assert isinstance(properties, dict)
        cast(dict[str, object], properties)["source_kind"] = "blog"
        return
    if case == "missing-issued-by":
        graph["edges"] = [
            edge
            for edge in _object_list(graph, "edges")
            if _object_at([edge], 0).get("label") != "Issued by"
        ]
        return
    raise AssertionError(f"caso de mutación desconocido: {case}")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong-schema", "schema"),
        ("duplicate-slug", "slug duplicado"),
        ("missing-knowledge-id", "knowledge_id"),
        ("duplicate-knowledge-id", "knowledge_id duplicado"),
        ("unresolved-edge", "sin resolver"),
        ("bad-source-kind", "source_kind"),
        ("missing-issued-by", "Issued by"),
    ],
)
async def test_incomplete_bundle_never_reaches_projection(
    case: str, message: str
) -> None:
    """S03.3 un bundle incompleto nunca llega a la proyección."""
    graph = _decoded_bundle()
    _mutate_invalid_bundle(graph, case)
    projection = InMemoryKnowledgeProjection()

    with pytest.raises(KnowledgeError, match=message):
        await activate_knowledge(json.dumps(graph).encode(), projection, imported_at=NOW)
    assert projection.writes == 0 and projection.current is None


async def test_projection_is_complete_traceable_and_idempotent(
    settings: Settings,
) -> None:
    """S03.4 la proyección activa es completa, trazable e idempotente."""
    await apply_schema(settings)
    projection = SurrealKnowledgeProjection(settings)
    first = await activate_knowledge(bundle_bytes(), projection, imported_at=NOW)
    second = await activate_knowledge(bundle_bytes(), projection, imported_at=NOW)

    assert first.changed and not second.changed
    snapshot = await projection.current_snapshot()
    assert snapshot is not None
    assert snapshot.source_head == "a" * 40
    assert snapshot.projection_version == PROJECTION_VERSION
    assert (snapshot.node_count, snapshot.edge_count, snapshot.warning_count) == (4, 3, 1)

    ledger = ledger_for(settings, "gateway")
    await ledger.connect()
    try:
        matches = await ledger.warnings_for(EntityKind.DOMAIN, "example-broker.test")
    finally:
        await ledger.close()
    assert [(item.id, item.regulator, item.active) for item in matches] == [
        ("demo-fca-example-broker", "FCA", True)
    ]


async def test_failed_activation_preserves_previous_snapshot(settings: Settings) -> None:
    """S03.5 un fallo conserva la versión activa anterior."""
    await apply_schema(settings)
    projection = SurrealKnowledgeProjection(settings)
    baseline_content = bundle_bytes(source_head="c" * 40)
    await activate_knowledge(baseline_content, projection, imported_at=NOW)
    previous = await projection.current_snapshot()
    assert previous is not None

    invalid_content = bundle_bytes(source_head="d" * 40, warning_status="withdrawn")
    invalid_bundle = parse_knowledge_bundle(invalid_content)
    warnings = warnings_from_bundle(invalid_bundle)
    invalid_snapshot = KnowledgeSnapshot(
        source_head=invalid_bundle.source_head,
        content_hash=invalid_bundle.content_hash,
        graph_schema=invalid_bundle.schema,
        profile=invalid_bundle.profile,
        projection_version=PROJECTION_VERSION,
        imported_at=NOW + timedelta(days=1),
        node_count=len(invalid_bundle.nodes),
        edge_count=len(invalid_bundle.edges),
        warning_count=2,
    )

    with pytest.raises(LedgerError):
        await projection.activate(
            invalid_snapshot,
            invalid_bundle,
            (warnings[0], warnings[0]),
        )
    assert await projection.current_snapshot() == previous


async def test_devcontainer_builds_serves_and_projects_knowledge(settings: Settings) -> None:
    """S03.6 el devcontainer construye, sirve y proyecta el catálogo al arrancar."""
    compose = Path(".devcontainer/docker-compose.yml").read_text(encoding="utf-8")
    devcontainer = json.loads(Path(".devcontainer/devcontainer.json").read_text(encoding="utf-8"))
    bootstrap = Path("argos/devtools/bootstrap_local.py").read_text(encoding="utf-8")

    assert re.search(r"^  knowledge:\n", compose, re.MULTILINE)
    assert "knowledge.Dockerfile" in compose
    assert "knowledge_cache:/cache" in compose
    assert 'profiles: ["docs"]' in compose
    assert "KNOWLEDGE_GRAPH_PATH: knowledge/dist/okf-graph.json" in compose
    assert "knowledge" not in devcontainer["runServices"]
    assert 8400 in devcontainer["forwardPorts"]
    assert "project_knowledge(settings)" in bootstrap
    report = await project_knowledge(settings)
    assert report.nodes == 12 and report.edges >= 9 and report.warnings == 3


def test_corpus_contains_only_public_synthetic_knowledge() -> None:
    """S03.7 el corpus separa conocimiento público de datos privados."""
    corpus = Path("knowledge")
    files = sorted(corpus.rglob("*.md"))
    assert files and not Path("tests/fixtures/synthetic_warnings.json").exists()
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)

    urls = re.findall(r'https://[^\s"\]]+', text)
    assert urls and all(".example/" in url or url.endswith(".example") for url in urls)
    for forbidden in ("tenant_id", "case_id", "document_id", "OPENAI_API_KEY"):
        assert forbidden not in text
