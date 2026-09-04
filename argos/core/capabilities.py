"""Capacidades públicas del gateway. Es lo único que Argos publica:
ni un especialista ni una pieza interna aparecen aquí."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityName(StrEnum):
    ANALYZE_NOTICE = "analyze_notice"
    GET_CASE = "get_case"
    ASK_CASE = "ask_case"


@dataclass(frozen=True)
class CapabilitySpec:
    name: CapabilityName
    method: str
    path: str
    description: str


GATEWAY_CAPABILITIES = (
    CapabilitySpec(
        name=CapabilityName.ANALYZE_NOTICE,
        method="POST",
        path="/v1/notices",
        description="Analiza un aviso breve y devuelve su caso con el veredicto.",
    ),
    CapabilitySpec(
        name=CapabilityName.GET_CASE,
        method="GET",
        path="/v1/cases/{case_id}",
        description="Devuelve el estado de un caso y su veredicto cuando existe.",
    ),
    CapabilitySpec(
        name=CapabilityName.ASK_CASE,
        method="POST",
        path="/v1/cases/{case_id}/questions",
        description="Responde una duda sobre un veredicto emitido con su evidencia.",
    ),
)

HEALTH_PATH = "/health"


def capability(name: CapabilityName) -> CapabilitySpec:
    return next(spec for spec in GATEWAY_CAPABILITIES if spec.name is name)
