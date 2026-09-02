"""Acceso HTTP a SurrealDB (/sql y /signin). Cáscara imperativa, sin reglas de negocio."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import httpx

Auth = tuple[str, str] | str
type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


class SurrealError(RuntimeError):
    pass


@dataclass(frozen=True)
class Statement:
    status: str
    result: JsonValue

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def parse_statements(payload: object) -> list[Statement]:
    if not isinstance(payload, list):
        raise SurrealError(f"Respuesta inesperada de /sql: {payload!r}")
    statements: list[Statement] = []
    for item in cast(list[object], payload):
        if not isinstance(item, dict):
            raise SurrealError(f"Entrada inesperada en /sql: {item!r}")
        entry = cast(dict[str, object], item)
        result = cast(JsonValue, entry.get("result"))
        statements.append(Statement(status=str(entry.get("status")), result=result))
    return statements


def failed_statements(statements: list[Statement]) -> list[Statement]:
    return [s for s in statements if not s.ok]


def auth_headers(auth: Auth) -> dict[str, str]:
    if isinstance(auth, str):
        return {"Authorization": f"Bearer {auth}"}
    return {}


class SurrealHttp:
    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def sql(
        self,
        query: str,
        *,
        auth: Auth,
        ns: str | None = None,
        db: str | None = None,
        raise_on_error: bool = True,
    ) -> list[Statement]:
        headers = {"Accept": "application/json", "Content-Type": "text/plain"}
        headers.update(auth_headers(auth))
        if ns:
            headers["surreal-ns"] = ns
        if db:
            headers["surreal-db"] = db
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            if isinstance(auth, tuple):
                response = await client.post(
                    f"{self._base_url}/sql", content=query, headers=headers, auth=auth
                )
            else:
                response = await client.post(
                    f"{self._base_url}/sql", content=query, headers=headers
                )
        if response.status_code != 200:
            raise SurrealError(f"/sql devolvió {response.status_code}: {response.text[:300]}")
        statements = parse_statements(cast(object, response.json()))
        failures = failed_statements(statements)
        if failures and raise_on_error:
            raise SurrealError(f"Sentencias fallidas: {[f.result for f in failures]}")
        return statements

    async def sign_in(self, *, ns: str, db: str, user: str, password: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/signin",
                json={"ns": ns, "db": db, "user": user, "pass": password},
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            raise SurrealError(f"/signin devolvió {response.status_code}: {response.text[:300]}")
        payload = cast(object, response.json())
        if not isinstance(payload, dict):
            raise SurrealError("/signin devolvió una respuesta inesperada")
        token = cast(dict[str, object], payload).get("token")
        if not isinstance(token, str) or not token:
            raise SurrealError("/signin no devolvió token")
        return token
