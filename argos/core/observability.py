"""Qué sale de Argos cuando algo falla (constitución §11): un código del catálogo,
nunca el detalle interno que lo produjo."""

from __future__ import annotations

INTERNAL_ERROR = "case.internal_error"

PUBLIC_ERRORS = frozenset(
    {
        INTERNAL_ERROR,
        "notice.text_too_long",
        "notice.too_many_links",
        "notice.image_too_large",
        "notice.image_unsupported",
        "notice.empty",
        "case.analysis_failed",
        "case.busy",
        "case.not_found",
        "tenant.unknown",
    }
)


def public_code(code: str | None) -> str:
    """Lo que no está en el catálogo no sale: el detalle se queda en el libro."""
    return code if code in PUBLIC_ERRORS else INTERNAL_ERROR
