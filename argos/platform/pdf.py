"""Lectura de PDF con pypdfium2: texto embebido y render de página."""

from __future__ import annotations

import io
from typing import Protocol, cast

import pypdfium2

from argos.core.ports import OpenPdf, PdfDamagedError, PdfEncryptedError

PASSWORD_MARKERS = ("password", "incorrect password")


class _Bitmap(Protocol):
    def to_pil(self) -> _Image: ...


class _Image(Protocol):
    def save(self, target: io.BytesIO, image_format: str, /) -> None: ...


class _Page(Protocol):
    def get_textpage(self) -> _TextPage: ...

    def render(self, scale: float) -> _Bitmap: ...


class _TextPage(Protocol):
    def get_text_bounded(self) -> str: ...


class _Document(Protocol):
    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> _Page: ...

    def close(self) -> None: ...


class PdfiumDocument:
    def __init__(self, document: _Document) -> None:
        self._document = document

    @property
    def page_count(self) -> int:
        return len(self._document)

    def text_of(self, number: int) -> str:
        page = self._document[number - 1]
        return page.get_textpage().get_text_bounded()

    def image_of(self, number: int, *, scale: float) -> bytes:
        page = self._document[number - 1]
        buffer = io.BytesIO()
        page.render(scale=scale).to_pil().save(buffer, "PNG")
        return buffer.getvalue()

    def close(self) -> None:
        self._document.close()


class PdfiumReader:
    def open(self, data: bytes) -> OpenPdf:
        try:
            document = cast(_Document, pypdfium2.PdfDocument(data))
        except pypdfium2.PdfiumError as error:
            if any(marker in str(error).lower() for marker in PASSWORD_MARKERS):
                raise PdfEncryptedError(str(error)) from error
            raise PdfDamagedError(str(error)) from error
        return PdfiumDocument(document)
