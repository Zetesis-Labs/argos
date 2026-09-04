"""Extracción y normalización de identificadores del aviso (R2).

Es una función pura y determinista: no hace falta un modelo para reconocer un
IBAN, un dominio o un teléfono, y hacerlo por código evita que una alucinación
entre en la memoria compartida.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from argos.core.analysis import DraftEntity
from argos.core.model import EntityKind, Strength

# Sufijos de dos etiquetas: sin ellos `banco.co.uk` se registraría como `co.uk`.
COMPOUND_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "net.au", "org.au",
        "com.br", "com.mx", "com.ar", "com.co", "com.pe", "com.tr", "co.jp",
        "co.kr", "co.za", "co.nz", "com.cn", "com.sg", "com.hk", "com.es",
    }
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}\b")
IBAN_RE = re.compile(
    r"\b[A-Za-z]{2}[0-9]{2}(?:[A-Za-z0-9]{11,30}"
    r"|(?:[ -][A-Za-z0-9]{4}){2,7}(?:[ -][A-Za-z0-9]{1,3})?)\b"
)
PHONE_RE = re.compile(r"(?<![\w.+])\+?[0-9][0-9 \-()]{7,18}[0-9](?!\d)")
HANDLE_RE = re.compile(r"(?<![\w@.])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_])?)\b")
WALLET_RE = re.compile(
    r"\b(?:0x[0-9a-fA-F]{40}|bc1[023456789acdefghjklmnpqrstuvwxyz]{25,62}"
    r"|[13][1-9A-HJ-NP-Za-km-z]{25,34})\b"
)

CASEFOLDED = (EntityKind.DOMAIN, EntityKind.EMAIL, EntityKind.HANDLE)
COMPACTED = (EntityKind.IBAN, EntityKind.PHONE)
DEFAULT_PHONE_PREFIX = "+34"
NATIONAL_PHONE_LENGTH = 9
NATIONAL_PHONE_HEADS = ("6", "7", "8", "9")


def registrable_domain(host: str) -> str:
    """El dominio que alguien registra: `pagos.banco.test` y `banco.test` son la misma entidad."""
    labels = host.strip(".").casefold().split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in COMPOUND_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def normalized_phone(raw: str) -> str | None:
    digits = re.sub(r"[^0-9+]", "", raw)
    if digits.startswith("+"):
        rest = digits[1:]
        return f"+{rest}" if rest.isdigit() and 8 <= len(rest) <= 15 else None
    if len(digits) == NATIONAL_PHONE_LENGTH and digits.startswith(NATIONAL_PHONE_HEADS):
        return f"{DEFAULT_PHONE_PREFIX}{digits}"
    return None


def iban_is_valid(candidate: str) -> bool:
    """Los dígitos de control (ISO 13616) descartan lo que solo parece un IBAN."""
    compact = candidate.upper()
    rotated = compact[4:] + compact[:4]
    expanded = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rotated)
    return expanded.isdigit() and int(expanded) % 97 == 1


def normalized_identifier(kind: EntityKind, value: str) -> str:
    trimmed = " ".join(value.split())
    if kind is EntityKind.DOMAIN:
        return registrable_domain(trimmed)
    if kind is EntityKind.EMAIL:
        local, _, host = trimmed.casefold().partition("@")
        return f"{local}@{host}" if host else local
    if kind is EntityKind.PHONE:
        return normalized_phone(trimmed) or re.sub(r"[^0-9+]", "", trimmed)
    if kind in CASEFOLDED:
        return trimmed.casefold()
    if kind in COMPACTED:
        return trimmed.replace(" ", "").replace("-", "").upper()
    if kind is EntityKind.WALLET:
        return trimmed.replace(" ", "").replace("-", "")
    return trimmed


def _blank(text: str, matches: Iterable[re.Match[str]]) -> str:
    """Consumir lo reconocido evita que un correo vuelva a salir como arroba o dominio."""
    blanked = list(text)
    for match in matches:
        for position in range(match.start(), match.end()):
            blanked[position] = " "
    return "".join(blanked)


def _host_of(url: str) -> str:
    without_scheme = url.split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return host.rpartition("@")[2].partition(":")[0]


def extract_identifiers(text: str, links: Sequence[str] = ()) -> tuple[DraftEntity, ...]:
    """Todo lo que el aviso menciona sin ambigüedad, ya normalizado y sin repetir."""
    found: list[tuple[EntityKind, str, Strength]] = []
    for url in links:
        host = _host_of(url)
        if DOMAIN_RE.fullmatch(host):
            found.append((EntityKind.DOMAIN, registrable_domain(host), Strength.STRONG))

    remaining = text
    for match in IBAN_RE.finditer(remaining):
        compact = normalized_identifier(EntityKind.IBAN, match.group())
        if iban_is_valid(compact):
            found.append((EntityKind.IBAN, compact, Strength.STRONG))
    remaining = _blank(remaining, IBAN_RE.finditer(remaining))

    for match in WALLET_RE.finditer(remaining):
        found.append((EntityKind.WALLET, match.group(), Strength.STRONG))
    remaining = _blank(remaining, WALLET_RE.finditer(remaining))

    for match in URL_RE.finditer(remaining):
        host = _host_of(match.group())
        if DOMAIN_RE.fullmatch(host):
            found.append((EntityKind.DOMAIN, registrable_domain(host), Strength.STRONG))
    remaining = _blank(remaining, URL_RE.finditer(remaining))

    for match in EMAIL_RE.finditer(remaining):
        address = normalized_identifier(EntityKind.EMAIL, match.group())
        found.append((EntityKind.EMAIL, address, Strength.STRONG))
        found.append((EntityKind.DOMAIN, registrable_domain(address.partition("@")[2]),
                      Strength.STRONG))
    remaining = _blank(remaining, EMAIL_RE.finditer(remaining))

    for match in HANDLE_RE.finditer(remaining):
        found.append((EntityKind.HANDLE, match.group(1).casefold(), Strength.WEAK))
    remaining = _blank(remaining, HANDLE_RE.finditer(remaining))

    for match in DOMAIN_RE.finditer(remaining):
        found.append((EntityKind.DOMAIN, registrable_domain(match.group()), Strength.STRONG))
    remaining = _blank(remaining, DOMAIN_RE.finditer(remaining))

    for match in PHONE_RE.finditer(remaining):
        number = normalized_phone(match.group())
        if number is not None:
            found.append((EntityKind.PHONE, number, Strength.STRONG))

    seen: dict[tuple[EntityKind, str], DraftEntity] = {}
    for kind, value, strength in found:
        if value and (kind, value) not in seen:
            seen[(kind, value)] = DraftEntity(kind=kind, value=value, strength=strength)
    return tuple(seen.values())
