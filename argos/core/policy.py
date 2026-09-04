"""Valores de R1, R15 y R21 con sus defectos de v1. Configurables hacia abajo (A6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class NoticeLimits:
    max_text_chars: int = 20_000
    max_links: int = 3
    max_image_bytes: int = 10 * 1024 * 1024


@dataclass(frozen=True)
class Retention:
    case: timedelta = timedelta(days=365)
    notice_dedup_window: timedelta = timedelta(hours=24)


@dataclass(frozen=True)
class AnalysisPolicy:
    budget: timedelta = timedelta(seconds=60)


@dataclass(frozen=True)
class Policy:
    notices: NoticeLimits = NoticeLimits()
    retention: Retention = Retention()
    analysis: AnalysisPolicy = AnalysisPolicy()
