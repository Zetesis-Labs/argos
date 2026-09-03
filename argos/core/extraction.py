"""Decisiones puras de la extracción: qué página necesita OCR, cómo se trocea y qué manifiesto
describe el resultado. Sin I/O: el worker aporta el texto y las imágenes (constitución §3)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum

from argos.core.ledger import ChunkDraft

WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
PARAGRAPH_RE = re.compile(r"\n\s*\n")


class TextSource(StrEnum):
    EMBEDDED = "embedded"
    OCR = "ocr"


@dataclass(frozen=True)
class PageText:
    number: int
    text: str
    source: TextSource


def normalize(text: str) -> str:
    collapsed = WHITESPACE_RE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [line.strip() for line in collapsed.split("\n")]
    return BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def is_usable(text: str, *, min_chars: int) -> bool:
    return len(normalize(text)) >= min_chars


def full_text(pages: tuple[PageText, ...]) -> str:
    return "\n\n".join(page.text for page in sorted(pages, key=lambda page: page.number))


def split_page(text: str, *, max_chars: int) -> list[str]:
    pieces: list[str] = []
    for paragraph in PARAGRAPH_RE.split(text):
        block = paragraph.strip()
        if not block:
            continue
        while len(block) > max_chars:
            cut = block.rfind(" ", 0, max_chars)
            edge = cut if cut > max_chars // 2 else max_chars
            pieces.append(block[:edge].strip())
            block = block[edge:].strip()
        if block:
            pieces.append(block)
    return pieces


def build_chunks(pages: tuple[PageText, ...], *, max_chars: int) -> tuple[ChunkDraft, ...]:
    chunks: list[ChunkDraft] = []
    for page in sorted(pages, key=lambda page: page.number):
        for piece in split_page(page.text, max_chars=max_chars):
            chunks.append(
                ChunkDraft(
                    page=page.number,
                    position=len(chunks),
                    text=piece,
                    sha256=hashlib.sha256(piece.encode()).hexdigest(),
                )
            )
    return tuple(chunks)


def ocr_pages(pages: tuple[PageText, ...]) -> int:
    return sum(1 for page in pages if page.source is TextSource.OCR)


def manifest(
    pages: tuple[PageText, ...],
    chunks: tuple[ChunkDraft, ...],
    *,
    extractor_version: str,
    options: str,
    text_sha256: str,
) -> bytes:
    document = {
        "extractor_version": extractor_version,
        "options": options,
        "page_count": len(pages),
        "ocr_pages": ocr_pages(pages),
        "text_sha256": text_sha256,
        "pages": [
            {"number": page.number, "source": page.source.value, "characters": len(page.text)}
            for page in sorted(pages, key=lambda page: page.number)
        ],
        "chunks": [
            {"position": chunk.position, "page": chunk.page, "sha256": chunk.sha256}
            for chunk in chunks
        ],
    }
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=None).encode()
