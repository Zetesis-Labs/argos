"""Capacidades públicas del gateway (S02 §5). Es lo único que Argos publica:
ni un especialista, ni el equipo, ni un worker aparecen aquí."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityName(StrEnum):
    ANALYZE_NOTICE = "analyze_notice"
    SUBMIT_DOCUMENT = "submit_document"
    GET_JOB = "get_job"
    GET_CASE = "get_case"
    ASK_CASE = "ask_case"
    REPROCESS_DOCUMENT = "reprocess_document"


@dataclass(frozen=True)
class CapabilitySpec:
    name: CapabilityName
    method: str
    path: str
    description: str
    curator_only: bool
    remote: bool


GATEWAY_CAPABILITIES = (
    CapabilitySpec(
        name=CapabilityName.ANALYZE_NOTICE,
        method="POST",
        path="/v1/notices",
        description="Analiza un aviso breve y devuelve su caso y, si llega a tiempo, su veredicto.",
        curator_only=False,
        remote=True,
    ),
    CapabilitySpec(
        name=CapabilityName.SUBMIT_DOCUMENT,
        method="POST",
        path="/v1/documents",
        description="Acepta un PDF para su caso y devuelve el trabajo que lo procesará.",
        curator_only=False,
        remote=False,
    ),
    CapabilitySpec(
        name=CapabilityName.GET_JOB,
        method="GET",
        path="/v1/jobs/{job_id}",
        description="Devuelve el estado público de un trabajo.",
        curator_only=False,
        remote=True,
    ),
    CapabilitySpec(
        name=CapabilityName.GET_CASE,
        method="GET",
        path="/v1/cases/{case_id}",
        description="Devuelve el estado de un caso y su veredicto cuando existe.",
        curator_only=False,
        remote=True,
    ),
    CapabilitySpec(
        name=CapabilityName.ASK_CASE,
        method="POST",
        path="/v1/cases/{case_id}/questions",
        description="Responde una duda sobre un veredicto emitido con su evidencia.",
        curator_only=False,
        remote=True,
    ),
    CapabilitySpec(
        name=CapabilityName.REPROCESS_DOCUMENT,
        method="POST",
        path="/v1/documents/{document_id}/reprocess",
        description="Reprocesa un documento creando un trabajo nuevo vinculado al anterior.",
        curator_only=True,
        remote=False,
    ),
)

# AgentOS sirve su propia salud en esta ruta; el gateway no la duplica.
HEALTH_PATH = "/health"
CARD_PATH = "/.well-known/agent-card.json"
MESSAGES_PATH = "/v1/a2a/messages"
# Fuera de /v1 a propósito: el guardián deja este plano al curador (S02 §13).
METRICS_PATH = "/metrics"


def capability(name: CapabilityName) -> CapabilitySpec:
    return next(spec for spec in GATEWAY_CAPABILITIES if spec.name is name)


def agent_card(*, name: str, version: str, url: str) -> dict[str, object]:
    """Tarjeta del gateway: sus habilidades son las capacidades, no los agentes."""
    return {
        "protocolVersion": "0.3.0",
        "name": name,
        "description": (
            "Segunda opinión ante un posible fraude financiero: analiza avisos y "
            "documentos y devuelve un veredicto explicado con evidencias."
        ),
        "version": version,
        "url": f"{url.rstrip('/')}{MESSAGES_PATH}",
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": str(spec.name),
                "name": str(spec.name),
                "description": spec.description,
                "tags": ["fraude", "veredicto"],
                "transport": "JSONRPC" if spec.remote else "HTTP",
                "endpoint": f"{spec.method} {spec.path}",
            }
            for spec in GATEWAY_CAPABILITIES
        ],
    }
