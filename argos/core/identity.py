"""Identidades del gateway (R16; constitución §6). El tenant sale de la credencial."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    SERVICE = "service"
    CURATOR = "curator"


@dataclass(frozen=True)
class Identity:
    """El curador opera el despliegue completo, no un tenant."""

    name: str
    role: Role
    tenant_id: str | None

    @property
    def is_curator(self) -> bool:
        return self.role is Role.CURATOR


def parse_registry(declaration: str) -> Mapping[str, Identity]:
    """`token=nombre:tenant` para un servicio y `token=nombre:curator` para el curador."""
    registry: dict[str, Identity] = {}
    for entry in declaration.split(","):
        token, _, described = entry.strip().partition("=")
        name, _, scope = described.partition(":")
        if not token or not name or not scope:
            continue
        registry[token] = (
            Identity(name=name, role=Role.CURATOR, tenant_id=None)
            if scope == Role.CURATOR
            else Identity(name=name, role=Role.SERVICE, tenant_id=scope)
        )
    return registry


def resolve(token: str | None, registry: Mapping[str, Identity]) -> Identity | None:
    return registry.get(token) if token else None


def bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None
