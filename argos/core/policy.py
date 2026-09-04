"""Valores de R1 y R15 con sus defectos. Configurables hacia abajo (A6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class NoticeLimits:
    max_text_chars: int = 20_000
    max_links: int = 3
    max_image_bytes: int = 10 * 1024 * 1024


@dataclass(frozen=True)
class AnalysisPolicy:
    budget: timedelta = timedelta(seconds=60)


@dataclass(frozen=True)
class Policy:
    notices: NoticeLimits = NoticeLimits()
    analysis: AnalysisPolicy = AnalysisPolicy()
