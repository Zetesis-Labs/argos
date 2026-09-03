"""Valores de R1, R18, R19 y R21 con sus defectos de v1. Configurables hacia abajo (A6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class DocumentLimits:
    max_bytes: int = 25 * 1024 * 1024
    max_pages: int = 500


@dataclass(frozen=True)
class NoticeLimits:
    max_text_chars: int = 20_000
    max_links: int = 3
    max_image_bytes: int = 10 * 1024 * 1024


@dataclass(frozen=True)
class Retention:
    full_content: timedelta = timedelta(days=30)
    case: timedelta = timedelta(days=365)
    staging: timedelta = timedelta(hours=6)
    notice_dedup_window: timedelta = timedelta(hours=24)


@dataclass(frozen=True)
class JobPolicy:
    max_attempts: int = 3
    lease: timedelta = timedelta(minutes=5)
    outbox_lease: timedelta = timedelta(seconds=30)
    backoff_base: timedelta = timedelta(seconds=30)
    backoff_factor: int = 4
    # Mayor que `outbox_lease`: cubre al dispatcher que publicó y murió antes de marcarlo.
    duplicate_window: timedelta = timedelta(minutes=5)
    max_deliveries: int = 3
    message_ttl: timedelta = timedelta(days=7)

    def backoff(self, failed_attempt: int) -> timedelta:
        multiplier = 1
        for _ in range(failed_attempt - 1):
            multiplier *= self.backoff_factor
        return self.backoff_base * multiplier


@dataclass(frozen=True)
class Policy:
    documents: DocumentLimits = DocumentLimits()
    notices: NoticeLimits = NoticeLimits()
    retention: Retention = Retention()
    jobs: JobPolicy = JobPolicy()
    extractor_version: str = "pdf-text-v1"
    extraction_options: str = "{}"
