"""Traducción pura entre el clúster de agentes y el núcleo: qué se le pide y cómo
se lee su respuesta. El prompt lleva referencias, nunca texto del documento (R8)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from argos.core.analysis import DraftEntity, DraftSignal, Evidence, normalized_identifier
from argos.core.model import Analysis, EntityKind, Strength
from argos.core.ports import CaseBrief, ConversationBrief, Investigation, VerdictBrief

INVESTIGATION_CONTRACT = (
    "Responde solo con un objeto JSON con las claves signals, entities y missing. "
    'Cada señal es {"analysis","code","strength","source","observed_at","value",'
    '"quote","official","recidivism"}, con analysis en '
    "[triage, registries, domain, patterns, memory, document], strength en "
    "[strong, weak] y observed_at en ISO 8601. Cada entidad es "
    '{"kind","value","strength"}, con kind en '
    "[domain, phone, email, iban, wallet, handle, company]. En missing pon el "
    "nombre de cada análisis que no hayas podido completar. No inventes "
    "evidencia: una señal sin fuente, fecha, valor y cita se descarta."
)


def investigation_prompt(brief: CaseBrief) -> str:
    lines = [
        f"Caso {brief.case_id}. Idioma de la respuesta: {brief.language}.",
        "Analiza los indicios de fraude financiero de este caso.",
    ]
    if brief.extractions:
        lines.append("Extracciones disponibles (pide sus fragmentos con tus herramientas):")
        lines.extend(
            f"- {reference.extraction_id} del documento {reference.document_id}, "
            f"{reference.page_count} páginas"
            for reference in brief.extractions
        )
    else:
        lines.append("No hay ninguna extracción disponible.")
    if brief.missing:
        lines.append(f"Entradas que no se pudieron procesar: {', '.join(brief.missing)}.")
    lines.append(INVESTIGATION_CONTRACT)
    return "\n".join(lines)


def verdict_prompt(brief: VerdictBrief) -> str:
    lines = [
        f"Caso {brief.case_id}. Escribe en {brief.language}.",
        f"Nivel ya calculado: {brief.level}. Desenlace: {brief.outcome}.",
        "Redacta dos o tres frases que expliquen ese nivel con sus indicios.",
        "Habla de indicios y coincidencias. No imputes delitos ni señales a personas.",
        "No cambies el nivel ni añadas acciones: ya están decididas.",
    ]
    if brief.signals:
        lines.append("Indicios:")
        lines.extend(
            f"- {signal.analysis}/{signal.code} ({signal.strength}) según "
            f"{signal.evidence.source}: {signal.evidence.quote}"
            for signal in brief.signals
        )
    else:
        lines.append("No hay ningún indicio sostenido por evidencia.")
    if brief.missing:
        lines.append(f"Di explícitamente que faltó: {', '.join(brief.missing)}.")
    return "\n".join(lines)


def fallback_summary(brief: VerdictBrief) -> str:
    absent = f" No se pudo completar: {', '.join(brief.missing)}." if brief.missing else ""
    return (
        f"Nivel {brief.level} con {len(brief.signals)} indicio(s) sostenido(s) por "
        f"evidencia.{absent}"
    ).strip()


def _object_in(text: str) -> dict[str, object] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        decoded = cast(object, json.loads(text[start : end + 1]))
    except ValueError:
        return None
    return cast(dict[str, object], decoded) if isinstance(decoded, dict) else None


def _items(report: dict[str, object], key: str) -> list[dict[str, object]]:
    raw = report.get(key)
    if not isinstance(raw, list):
        return []
    return [
        cast(dict[str, object], item) for item in cast(list[object], raw) if isinstance(item, dict)
    ]


def _text(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    return value if isinstance(value, str) else ""


def _flag(item: dict[str, object], key: str) -> bool:
    value = item.get(key)
    return value is True


def _observed_at(item: dict[str, object]) -> datetime | None:
    raw = _text(item, "observed_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _member[E: (Analysis, Strength, EntityKind)](values: type[E], raw: str) -> E | None:
    try:
        return values(raw)
    except ValueError:
        return None


def _signal(item: dict[str, object]) -> DraftSignal | None:
    analysis = _member(Analysis, _text(item, "analysis"))
    strength = _member(Strength, _text(item, "strength"))
    if analysis is None or strength is None:
        return None
    return DraftSignal(
        analysis=analysis,
        code=_text(item, "code") or "unspecified",
        strength=strength,
        evidence=Evidence(
            source=_text(item, "source"),
            observed_at=_observed_at(item),
            value=_text(item, "value"),
            quote=_text(item, "quote"),
        ),
        official=_flag(item, "official"),
        recidivism=_flag(item, "recidivism"),
    )


def _entity(item: dict[str, object]) -> DraftEntity | None:
    kind = _member(EntityKind, _text(item, "kind"))
    value = _text(item, "value").strip()
    if kind is None or not value:
        return None
    strength = _member(Strength, _text(item, "strength"))
    return DraftEntity(
        kind=kind,
        value=normalized_identifier(kind, value),
        strength=Strength.WEAK if kind is EntityKind.COMPANY else (strength or Strength.STRONG),
    )


def _missing(report: dict[str, object]) -> tuple[str, ...]:
    raw = report.get("missing")
    if not isinstance(raw, list):
        return ()
    named = [item for item in cast(list[object], raw) if isinstance(item, str) and item.strip()]
    return tuple(dict.fromkeys(item.strip() for item in named))


def parse_investigation(text: str, *, expected: Sequence[Analysis]) -> Investigation:
    """Una respuesta que no cumple el contrato no produce señales: todo lo esperado falta."""
    report = _object_in(text)
    if report is None:
        return Investigation(signals=(), entities=(), missing=tuple(str(name) for name in expected))
    signals = tuple(
        signal for item in _items(report, "signals") if (signal := _signal(item)) is not None
    )
    entities = tuple(
        entity for item in _items(report, "entities") if (entity := _entity(item)) is not None
    )
    return Investigation(signals=signals, entities=entities, missing=_missing(report))


NO_VERDICT_YET = (
    "Todavía no hay veredicto para este caso: cuando termine su análisis podrás consultarlo aquí."
)


def conversation_prompt(brief: ConversationBrief) -> str:
    lines = [
        f"Caso {brief.case_id}. Responde en {brief.language}.",
        f"Veredicto emitido: nivel {brief.level}, desenlace {brief.outcome}.",
        f"Explicación registrada: {brief.summary}",
        "Acciones ya recomendadas:",
        *(f"- {action}" for action in brief.actions),
    ]
    if brief.quotes:
        lines.append("Evidencia conservada:")
        lines.extend(f"- {quote}" for quote in brief.quotes)
    lines.extend(
        (
            "Responde solo con esta evidencia. No cambies el nivel ni añadas indicios.",
            "Si piden asesoramiento financiero o jurídico, decline y recuerda el alcance.",
            "Si aportan datos nuevos, ofrece analizarlos como caso nuevo vinculado.",
            f"Pregunta: {brief.question}",
        )
    )
    return "\n".join(lines)


def fallback_answer(brief: ConversationBrief) -> str:
    return (
        f"El caso mantiene el nivel {brief.level}. {brief.summary} "
        f"Sigue estas acciones: {'; '.join(brief.actions)}"
    ).strip()
