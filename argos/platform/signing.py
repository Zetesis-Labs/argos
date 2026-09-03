"""Firma AWS SigV4 para el almacén S3. Funciones puras: misma entrada, misma firma."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from urllib.parse import quote

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
SERVICE = "s3"


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def signing_key(secret: str, *, datestamp: str, region: str) -> bytes:
    initial = f"AWS4{secret}".encode()
    return _hmac(_hmac(_hmac(_hmac(initial, datestamp), region), SERVICE), "aws4_request")


def encode_path(path: str) -> str:
    return quote(path, safe="/~")


def encode_query(params: dict[str, str]) -> str:
    return "&".join(
        f"{quote(name, safe='~')}={quote(params[name], safe='~')}" for name in sorted(params)
    )


def canonical_request(
    *,
    method: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
) -> tuple[str, str]:
    normalized = {name.lower(): value.strip() for name, value in headers.items()}
    signed = ";".join(sorted(normalized))
    canonical_headers = "".join(f"{name}:{normalized[name]}\n" for name in sorted(normalized))
    request = "\n".join(
        (
            method,
            encode_path(path),
            encode_query(query),
            canonical_headers,
            signed,
            payload_hash,
        )
    )
    return request, signed


def credential_scope(datestamp: str, region: str) -> str:
    return f"{datestamp}/{region}/{SERVICE}/aws4_request"


def string_to_sign(request: str, *, amz_date: str, scope: str) -> str:
    digest = hashlib.sha256(request.encode()).hexdigest()
    return "\n".join((ALGORITHM, amz_date, scope, digest))


def sign(
    *,
    method: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
    secret: str,
    datestamp: str,
    amz_date: str,
    region: str,
) -> str:
    request, _ = canonical_request(
        method=method, path=path, query=query, headers=headers, payload_hash=payload_hash
    )
    scope = credential_scope(datestamp, region)
    to_sign = string_to_sign(request, amz_date=amz_date, scope=scope)
    key = signing_key(secret, datestamp=datestamp, region=region)
    return hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()


def timestamps(now: datetime) -> tuple[str, str]:
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    return amz_date, amz_date[:8]


def signed_headers(
    *,
    method: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
    access_key: str,
    secret: str,
    region: str,
    now: datetime,
) -> dict[str, str]:
    amz_date, datestamp = timestamps(now)
    complete = {**headers, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash}
    request, signed = canonical_request(
        method=method, path=path, query=query, headers=complete, payload_hash=payload_hash
    )
    scope = credential_scope(datestamp, region)
    to_sign = string_to_sign(request, amz_date=amz_date, scope=scope)
    key = signing_key(secret, datestamp=datestamp, region=region)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"{ALGORITHM} Credential={access_key}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    return {**complete, "authorization": authorization}


def presigned_query(
    *,
    method: str,
    path: str,
    host: str,
    access_key: str,
    secret: str,
    region: str,
    now: datetime,
    expires_in: int,
) -> str:
    amz_date, datestamp = timestamps(now)
    query = {
        "X-Amz-Algorithm": ALGORITHM,
        "X-Amz-Credential": f"{access_key}/{credential_scope(datestamp, region)}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_in),
        "X-Amz-SignedHeaders": "host",
    }
    signature = sign(
        method=method,
        path=path,
        query=query,
        headers={"host": host},
        payload_hash=UNSIGNED_PAYLOAD,
        secret=secret,
        datestamp=datestamp,
        amz_date=amz_date,
        region=region,
    )
    return f"{encode_query(query)}&X-Amz-Signature={signature}"
