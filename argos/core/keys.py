"""Claves de objetos en el almacén privado (S02 §10). No son autorización."""

from __future__ import annotations


def source_document_key(tenant_id: str, case_id: str, document_id: str) -> str:
    return f"tenants/{tenant_id}/cases/{case_id}/documents/{document_id}/source.pdf"


def extraction_text_key(tenant_id: str, case_id: str, extraction_id: str) -> str:
    return f"tenants/{tenant_id}/cases/{case_id}/extractions/{extraction_id}/text.txt.zst"


def extraction_manifest_key(tenant_id: str, case_id: str, extraction_id: str) -> str:
    return f"tenants/{tenant_id}/cases/{case_id}/extractions/{extraction_id}/manifest.json"
