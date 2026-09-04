"""Gateway HTTP (S02 §5). Autentica, deriva el tenant de la identidad y devuelve
estado. No expone especialistas ni credenciales internas."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from argos.api.payloads import as_object, optional_text, strings_of, text_of
from argos.core.capabilities import HEALTH_PATH, CapabilityName, capability
from argos.core.identity import Identity, bearer_token, resolve
from argos.core.notices import Notice
from argos.core.observability import public_code
from argos.core.ports import CaseAdvisor
from argos.usecases.deps import Bookkeeping
from argos.usecases.gateway import (
    CaseAnswer,
    Investigators,
    analyze_notice,
    ask_case,
)
from argos.usecases.notices import NoticeRefused
from argos.usecases.queries import CaseView, VerdictSummary, get_case

Advisors = Callable[[str, str], CaseAdvisor]
Handler = Callable[["Gateway", Request, Identity], Awaitable[Response]]
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


@dataclass(frozen=True)
class Gateway:
    services: Bookkeeping
    investigators: Investigators
    advisors: Advisors
    identities: Mapping[str, Identity]
    version: str


def verdict_payload(verdict: VerdictSummary | None) -> dict[str, object] | None:
    if verdict is None:
        return None
    return {
        "version": verdict.version,
        "level": str(verdict.level),
        "outcome": str(verdict.outcome),
        "summary": verdict.summary,
        "actions": list(verdict.actions),
        "missing": list(verdict.missing),
    }


def case_payload(view: CaseView) -> dict[str, object]:
    return {
        "case_id": view.id,
        "state": str(view.state),
        "previous_case_id": view.previous_case_id,
        "review_state": str(view.review_state),
        "public_error": None if view.public_error is None else public_code(view.public_error),
        "verdict": verdict_payload(view.verdict),
    }


def answer_payload(answer: CaseAnswer) -> dict[str, object]:
    return {
        "case_id": answer.case_id,
        "answer": answer.answer,
        "verdict": verdict_payload(answer.verdict),
    }


def refusal(code: str, *, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


class UnauthenticatedError(Exception):
    pass


def identity_in(request: Request, identities: Mapping[str, Identity]) -> Identity:
    identity = resolve(bearer_token(request.headers.get("authorization")), identities)
    if identity is None:
        raise UnauthenticatedError
    return identity


async def serve(gateway: Gateway, request: Request, handler: Handler) -> Response:
    try:
        identity = identity_in(request, gateway.identities)
    except UnauthenticatedError:
        return refusal("identity.unknown", status=401)
    if identity.tenant_id is None:
        return refusal("identity.not_a_tenant", status=403)
    return await handler(gateway, request, identity)


async def analyze(gateway: Gateway, request: Request, identity: Identity) -> Response:
    tenant_id = identity.tenant_id or ""
    fields = as_object(await request.body())
    if fields is None:
        return refusal("request.malformed", status=400)
    analysis = await analyze_notice(
        gateway.services,
        gateway.investigators,
        tenant_id=tenant_id,
        notice=Notice(
            text=text_of(fields, "text"),
            links=strings_of(fields, "links"),
            language_hint=optional_text(fields, "language"),
        ),
        correlation_id=optional_text(fields, "correlation_id") or identity.name,
    )
    if isinstance(analysis, NoticeRefused):
        return refusal(analysis.code, status=422)
    return JSONResponse(
        content={
            "case_id": analysis.case_id,
            "state": str(analysis.state),
            "reused": analysis.reused,
            "verdict": verdict_payload(analysis.verdict),
        }
    )


async def show_case(gateway: Gateway, identity: Identity, case_id: str) -> Response:
    view = await get_case(gateway.services, tenant_id=identity.tenant_id or "", case_id=case_id)
    if view is None:
        return refusal("case.not_found", status=404)
    return JSONResponse(content=case_payload(view))


async def answer_question(
    gateway: Gateway, request: Request, identity: Identity, case_id: str
) -> Response:
    tenant_id = identity.tenant_id or ""
    fields = as_object(await request.body())
    if fields is None:
        return refusal("request.malformed", status=400)
    answer = await ask_case(
        gateway.services,
        gateway.advisors(tenant_id, case_id),
        tenant_id=tenant_id,
        case_id=case_id,
        question=text_of(fields, "question"),
    )
    if answer is None:
        return refusal("case.not_found", status=404)
    return JSONResponse(content=answer_payload(answer))


def build_app(gateway: Gateway, *, lifespan: Lifespan | None = None) -> FastAPI:
    app = FastAPI(title="Argos", version=gateway.version, lifespan=lifespan)

    async def health() -> JSONResponse:
        return JSONResponse(content={"status": "ok", "version": gateway.version})

    async def notices(request: Request) -> Response:
        return await serve(gateway, request, analyze)

    async def cases(request: Request, case_id: str) -> Response:
        async def handler(wired: Gateway, _: Request, identity: Identity) -> Response:
            return await show_case(wired, identity, case_id)

        return await serve(gateway, request, handler)

    async def questions(request: Request, case_id: str) -> Response:
        async def handler(wired: Gateway, given: Request, identity: Identity) -> Response:
            return await answer_question(wired, given, identity, case_id)

        return await serve(gateway, request, handler)

    app.add_api_route(HEALTH_PATH, health, methods=["GET"])
    for spec, endpoint in (
        (capability(CapabilityName.ANALYZE_NOTICE), notices),
        (capability(CapabilityName.GET_CASE), cases),
        (capability(CapabilityName.ASK_CASE), questions),
    ):
        app.add_api_route(spec.path, endpoint, methods=[spec.method])
    return app
