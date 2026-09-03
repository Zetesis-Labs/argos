"""Puntuación y composición del veredicto (R3-R7, R14). El LLM no entra aquí."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from argos.core.model import (
    Analysis,
    EntityKind,
    ReviewState,
    RiskLevel,
    Strength,
    VerdictOutcome,
)

SPANISH = "es"
ENGLISH = "en"
SUPPORTED_LANGUAGES = (SPANISH, ENGLISH)

RANKED = (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)

URGENT_ACTIONS = (
    "No envíes dinero ni datos personales a este contacto.",
    "Corta el contacto y no respondas a nuevos mensajes.",
    "Si ya hiciste un pago, avisa hoy mismo a tu banco.",
    "Denuncia ante la Policía Nacional o la Guardia Civil.",
    "Comunica el caso a la CNMV.",
)
CAUTIOUS_ACTIONS = (
    "No envíes dinero ni datos personales todavía.",
    "Comprueba la entidad en los registros oficiales de la CNMV antes de operar.",
    "Desconfía de la urgencia y de cualquier rentabilidad garantizada.",
)
ROUTINE_ACTIONS = (
    "Mantén la cautela habitual: no compartas claves ni datos personales.",
    "Comprueba la entidad en los registros oficiales de la CNMV antes de operar.",
)
UNDETERMINED_ACTIONS = (
    "No envíes dinero ni datos personales mientras no haya una evaluación.",
    "Repite la consulta más tarde con una entrada más completa.",
)

ACTIONS: dict[RiskLevel, tuple[str, ...]] = {
    RiskLevel.CRITICAL: URGENT_ACTIONS,
    RiskLevel.HIGH: URGENT_ACTIONS,
    RiskLevel.MEDIUM: CAUTIOUS_ACTIONS,
    RiskLevel.LOW: ROUTINE_ACTIONS,
    RiskLevel.UNDETERMINED: UNDETERMINED_ACTIONS,
}


@dataclass(frozen=True)
class Evidence:
    """R3: sin fuente, fecha, valor y cita la señal no existe."""

    source: str
    observed_at: datetime | None
    value: str
    quote: str


@dataclass(frozen=True)
class DraftSignal:
    analysis: Analysis
    code: str
    strength: Strength
    evidence: Evidence
    official: bool = False
    recidivism: bool = False


@dataclass(frozen=True)
class DraftEntity:
    kind: EntityKind
    value: str
    strength: Strength


@dataclass(frozen=True)
class CaseAppearance:
    case_id: str
    tenant_id: str
    review_state: ReviewState
    seen_at: datetime


@dataclass(frozen=True)
class EntityHistory:
    """R29: lo único que un tenant recibe de la memoria compartida."""

    kind: EntityKind
    value: str
    cases: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    confirmed: bool


@dataclass(frozen=True)
class Assessment:
    outcome: VerdictOutcome
    level: RiskLevel
    actions: tuple[str, ...]
    missing: tuple[str, ...]


def supported(signal: DraftSignal) -> bool:
    evidence = signal.evidence
    return bool(
        evidence.source.strip()
        and evidence.observed_at is not None
        and evidence.value.strip()
        and evidence.quote.strip()
    )


def usable(signals: Sequence[DraftSignal]) -> tuple[DraftSignal, ...]:
    return tuple(signal for signal in signals if supported(signal))


def at_least(level: RiskLevel, floor: RiskLevel) -> RiskLevel:
    return level if RANKED.index(level) >= RANKED.index(floor) else floor


def score(signals: Sequence[DraftSignal], *, degraded: bool) -> RiskLevel:
    if not signals:
        return RiskLevel.UNDETERMINED if degraded else RiskLevel.LOW
    # La marca la pone quien observa: oficial vigente sobre identificador fuerte o
    # nombre exacto, y reincidencia fuerte de un caso confirmado (R4).
    if any(signal.official or signal.recidivism for signal in signals):
        return RiskLevel.CRITICAL
    strong = [signal for signal in signals if signal.strength is Strength.STRONG]
    weak = len(signals) - len(strong)
    if len({signal.analysis for signal in strong}) >= 2:
        level = RiskLevel.HIGH
    elif strong or weak >= 3:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW
    return at_least(level, RiskLevel.MEDIUM) if degraded else level


def assess(
    signals: Sequence[DraftSignal], *, missing: Sequence[str], analyzable: bool
) -> Assessment:
    absent = tuple(missing)
    if not analyzable:
        return Assessment(
            outcome=VerdictOutcome.INSUFFICIENT,
            level=RiskLevel.UNDETERMINED,
            actions=ACTIONS[RiskLevel.UNDETERMINED],
            missing=absent,
        )
    level = score(usable(signals), degraded=bool(absent))
    outcome = VerdictOutcome.PARTIAL if absent else VerdictOutcome.ISSUED
    return Assessment(outcome=outcome, level=level, actions=ACTIONS[level], missing=absent)


def verdict_language(hint: str | None) -> str:
    return hint if hint in SUPPORTED_LANGUAGES else SPANISH


def aggregate_history(
    kind: EntityKind, value: str, appearances: Sequence[CaseAppearance]
) -> EntityHistory:
    counted = [
        appearance
        for appearance in appearances
        if appearance.review_state is not ReviewState.FALSE_POSITIVE
    ]
    seen = sorted(appearance.seen_at for appearance in counted)
    return EntityHistory(
        kind=kind,
        value=value,
        cases=len(counted),
        first_seen_at=seen[0] if seen else None,
        last_seen_at=seen[-1] if seen else None,
        confirmed=any(appearance.review_state is ReviewState.CONFIRMED for appearance in counted),
    )


CASEFOLDED = (EntityKind.DOMAIN, EntityKind.EMAIL, EntityKind.HANDLE)
COMPACTED = (EntityKind.IBAN, EntityKind.PHONE, EntityKind.WALLET)


def normalized_identifier(kind: EntityKind, value: str) -> str:
    """Lo mínimo para que la memoria compartida no se parta por mayúsculas o
    espacios. Todavía no es R2: falta el dominio registrable, el prefijo
    telefónico por defecto y los dígitos de control del IBAN."""
    trimmed = " ".join(value.split())
    if kind in CASEFOLDED:
        return trimmed.casefold()
    if kind in COMPACTED:
        compact = trimmed.replace(" ", "").replace("-", "")
        return compact.upper() if kind is EntityKind.IBAN else compact
    return trimmed
