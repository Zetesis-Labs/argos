"""Validación barata de un documento antes de encolar (R19)."""

from __future__ import annotations

from dataclasses import dataclass

from argos.core.policy import DocumentLimits

PDF_SIGNATURE = b"%PDF-"
PDF_MIME = "application/pdf"


@dataclass(frozen=True)
class UploadRejected:
    code: str


@dataclass(frozen=True)
class UploadAccepted:
    mime: str


def validate_upload(
    *, filename: str, declared_mime: str, head: bytes, size: int, limits: DocumentLimits
) -> UploadAccepted | UploadRejected:
    if not filename.lower().endswith(".pdf"):
        return UploadRejected("document.bad_extension")
    if declared_mime.split(";")[0].strip().lower() != PDF_MIME:
        return UploadRejected("document.bad_mime")
    if size > limits.max_bytes:
        return UploadRejected("document.too_large")
    if size <= 0 or not head.startswith(PDF_SIGNATURE):
        return UploadRejected("document.not_pdf")
    return UploadAccepted(mime=PDF_MIME)
