"""Señales que no puede producir un modelo (R4, constitución §5).

Una advertencia oficial vigente y una reincidencia confirmada llevan un caso a
`critical` por sí solas. Por eso salen de una consulta al catálogo proyectado y
a la memoria compartida, nunca de lo que el investigador diga haber encontrado.
"""

from __future__ import annotations

from collections.abc import Sequence

from argos.core.analysis import (
    CaseAppearance,
    DraftEntity,
    DraftSignal,
    Evidence,
    aggregate_history,
)
from argos.core.model import Analysis, EntityKind, Strength, entity_id
from argos.usecases.deps import Bookkeeping

OFFICIAL_WARNING = "official_warning"
PRIOR_CONFIRMED_CASE = "prior_confirmed_case"
MEMORY_SOURCE = "argos:memoria-compartida"
# R4 exige un identificador fuerte: un alias o un nombre de empresa no basta.
DECISIVE_KINDS = frozenset(
    {
        EntityKind.DOMAIN,
        EntityKind.EMAIL,
        EntityKind.IBAN,
        EntityKind.PHONE,
        EntityKind.WALLET,
    }
)


def _decisive(entity: DraftEntity) -> bool:
    return entity.kind in DECISIVE_KINDS and entity.strength is Strength.STRONG


async def official_signals(
    services: Bookkeeping, entities: Sequence[DraftEntity]
) -> tuple[DraftSignal, ...]:
    signals: list[DraftSignal] = []
    for entity in entities:
        if not _decisive(entity):
            continue
        for warning in await services.ledger.warnings_for(entity.kind, entity.value):
            if not warning.active:
                continue
            signals.append(
                DraftSignal(
                    analysis=Analysis.REGISTRIES,
                    code=OFFICIAL_WARNING,
                    strength=Strength.STRONG,
                    evidence=Evidence(
                        source=warning.url,
                        observed_at=warning.captured_at,
                        value=entity.value,
                        quote=(
                            f"{warning.regulator} mantiene una advertencia vigente "
                            f"sobre {entity.value}."
                        ),
                    ),
                    official=True,
                )
            )
    return tuple(signals)


async def recidivism_signals(
    services: Bookkeeping, entities: Sequence[DraftEntity], *, case_id: str
) -> tuple[DraftSignal, ...]:
    """Solo agregados: el tenant nunca ve el caso ajeno que sostiene la reincidencia."""
    signals: list[DraftSignal] = []
    for entity in entities:
        if not _decisive(entity):
            continue
        stored = await services.ledger.entity_by_value(entity.kind, entity.value)
        if stored is None:
            continue
        appearances: list[CaseAppearance] = []
        for link in await services.ledger.cases_of_entity(entity_id(entity.kind, entity.value)):
            if link.case_id == case_id:
                continue
            previous = await services.ledger.case(link.case_id)
            if previous is None:
                continue
            appearances.append(
                CaseAppearance(
                    case_id=previous.id,
                    tenant_id=previous.tenant_id,
                    review_state=previous.review_state,
                    seen_at=previous.created_at,
                )
            )
        history = aggregate_history(entity.kind, entity.value, appearances)
        if not history.confirmed:
            continue
        signals.append(
            DraftSignal(
                analysis=Analysis.MEMORY,
                code=PRIOR_CONFIRMED_CASE,
                strength=Strength.STRONG,
                evidence=Evidence(
                    source=MEMORY_SOURCE,
                    observed_at=history.last_seen_at,
                    value=entity.value,
                    quote=(
                        f"{entity.value} ya aparece en {history.cases} caso(s) previos, "
                        "con al menos uno confirmado como fraude."
                    ),
                ),
                recidivism=True,
            )
        )
    return tuple(signals)
