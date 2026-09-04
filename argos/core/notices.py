"""Límites del aviso (R1)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from argos.core.policy import NoticeLimits

IMAGE_SIGNATURES: dict[str, bytes] = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",
}
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Notice:
    text: str
    links: tuple[str, ...] = ()
    image: bytes | None = None
    image_mime: str | None = None
    language_hint: str | None = None


@dataclass(frozen=True)
class NoticeRejected:
    code: str


@dataclass(frozen=True)
class NoticeAccepted:
    pass


def normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip().casefold()


def image_mime_of(image: bytes) -> str | None:
    for mime, signature in IMAGE_SIGNATURES.items():
        if image.startswith(signature):
            if mime == "image/webp" and image[8:12] != b"WEBP":
                continue
            return mime
    return None


def validate_notice(notice: Notice, limits: NoticeLimits) -> NoticeAccepted | NoticeRejected:
    if len(notice.text) > limits.max_text_chars:
        return NoticeRejected("notice.text_too_long")
    if len(notice.links) > limits.max_links:
        return NoticeRejected("notice.too_many_links")
    if notice.image is not None:
        if len(notice.image) > limits.max_image_bytes:
            return NoticeRejected("notice.image_too_large")
        if image_mime_of(notice.image) is None:
            return NoticeRejected("notice.image_unsupported")
    if not normalize_text(notice.text) and not notice.links and notice.image is None:
        return NoticeRejected("notice.empty")
    return NoticeAccepted()
