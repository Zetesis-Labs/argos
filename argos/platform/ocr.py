"""OCR de una página con Tesseract. Solo se invoca en páginas sin texto utilizable."""

from __future__ import annotations

import io
from typing import Protocol, cast

import pytesseract
from PIL import Image


class _Tesseract(Protocol):
    def image_to_string(self, image: Image.Image, lang: str) -> str: ...


class TesseractOcr:
    def text_of(self, image: bytes, *, language: str) -> str:
        with Image.open(io.BytesIO(image)) as page:
            return cast(_Tesseract, pytesseract).image_to_string(page, lang=language)
