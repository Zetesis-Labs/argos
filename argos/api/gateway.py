"""Gateway HTTP del AgentOS (S02 §5). Autentica, deriva el tenant de la identidad
y devuelve estado. No expone especialistas, workers ni credenciales internas."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass

from fastapi import FastAPI, Request
from starlette.datastructures import UploadFile
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from argos.api.payloads import (
    as_object,
    form_text,
    form_upload,
    object_of,
    optional_text,
    strings_in,
    strings_of,
    text_of,
)
from argos.core.capabilities import (
    CARD_PATH,
    GATEWAY_CAPABILITIES,
    HEALTH_PATH,
    MESSAGES_PATH,
    CapabilityName,
    agent_card,
    capability,
)
from argos.core.identity import Identity, bearer_token, resolve
from argos.core.notices import Notice
from argos.core.ports import CaseAdvisor
from argos.core.reprocess import ReprocessRefused
from argos.usecases.deps import Services
from argos.usecases.documents import DocumentAccepted, DocumentRejected, DocumentUpload
from argos.usecases.documents import submit_document as submit_document_case
from argos.usecases.gateway import (
    CaseAnswer,
    Sleep,
    analyze_notice,
    ask_case,
    reprocess_document,
)
from argos.usecases.notices import NoticeRefused
from argos.usecases.queries import CaseView, JobView, VerdictSummary, get_case, get_job

UPLOAD_CHUNK = 64 * 1024
PUBLIC_PATHS = (HEALTH_PATH, CARD_PATH, "/docs", "/redoc", "/openapi.json")
TENANT_PREFIX = "/v1/"

Advisors = Callable[[str, str], CaseAdvisor]
Dispatch = Callable[[Request], Awaitable[Response]]


@dataclass(frozen=True)
class Gateway:
    services: Services
    advisors: Advisors
    identities: Mapping[str, Identity]
    version: str
    public_url: str
    sleep: Sleep = asyncio.sleep


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
        "verdict": verdict_payload(view.verdict),
    }


def job_payload(view: JobView) -> dict[str, object]:
    return {
        "job_id": view.id,
        "case_id": view.case_id,
        "document_id": view.document_id,
        "type": str(view.type),
        "state": str(view.state),
        "attempt": view.attempt,
        "public_error": view.public_error,
    }


def answer_payload(answer: CaseAnswer) -> dict[str, object]:
    return {
        "case_id": answer.case_id,
        "answer": answer.answer,
        "verdict": verdict_payload(answer.verdict),
    }


def accepted_payload(accepted: DocumentAccepted) -> dict[str, object]:
    return {
        "case_id": accepted.case_id,
        "document_id": accepted.document_id,
        "job_id": accepted.job_id,
        "job_state": str(accepted.job_state),
        "reused": accepted.reused,
    }


def refusal(code: str, *, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


async def upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await upload.read(UPLOAD_CHUNK):
        yield chunk


class UnauthenticatedError(Exception):
    pass


def identity_in(request: Request, identities: Mapping[str, Identity]) -> Identity:
    identity = resolve(bearer_token(request.headers.get("authorization")), identities)
    if identity is None:
        raise UnauthenticatedError
    return identity


Handler = Callable[[Gateway, Request, Identity], Awaitable[Response]]


async def serve(gateway: Gateway, request: Request, handler: Handler) -> Response:
    try:
        identity = identity_in(request, gateway.identities)
    except UnauthenticatedError:
        return refusal("identity.unknown", status=401)
    return await handler(gateway, request, identity)


async def analyze(gateway: Gateway, request: Request, identity: Identity) -> Response:
    if identity.tenant_id is None:
        return refusal("identity.not_a_tenant", status=403)
    fields = as_object(await request.body())
    if fields is None:
        return refusal("request.malformed", status=400)
    analysis = await analyze_notice(
        gateway.services,
        tenant_id=identity.tenant_id,
        notice=Notice(
            text=text_of(fields, "text"),
            links=strings_of(fields, "links"),
            language_hint=optional_text(fields, "language"),
        ),
        correlation_id=optional_text(fields, "correlation_id") or identity.name,
        sleep=gateway.sleep,
    )
    if isinstance(analysis, NoticeRefused):
        return refusal(analysis.code, status=422)
    return JSONResponse(
        status_code=200 if analysis.settled else 202,
        content={
            "case_id": analysis.case_id,
            "job_id": analysis.job_id,
            "state": str(analysis.state),
            "reused": analysis.reused,
            "verdict": verdict_payload(analysis.verdict),
        },
    )


async def submit(gateway: Gateway, request: Request, identity: Identity) -> Response:
    if identity.tenant_id is None:
        return refusal("identity.not_a_tenant", status=403)
    form = await request.form()
    upload = form_upload(form.get("file"))
    if upload is None:
        return refusal("document.missing", status=422)
    accepted = await submit_document_case(
        gateway.services,
        DocumentUpload(
            tenant_id=identity.tenant_id,
            case_id=form_text(form.get("case_id")),
            filename=upload.filename or "",
            declared_mime=upload.content_type or "",
            size=upload.size or 0,
            content=upload_chunks(upload),
            correlation_id=form_text(form.get("correlation_id")) or identity.name,
        ),
    )
    if isinstance(accepted, DocumentRejected):
        return refusal(accepted.code, status=422)
    return JSONResponse(status_code=202, content=accepted_payload(accepted))


async def show_job(gateway: Gateway, identity: Identity, job_id: str) -> Response:
    if identity.tenant_id is None:
        return refusal("identity.not_a_tenant", status=403)
    view = await get_job(gateway.services, tenant_id=identity.tenant_id, job_id=job_id)
    if view is None:
        return refusal("job.not_found", status=404)
    return JSONResponse(content=job_payload(view))


async def show_case(gateway: Gateway, identity: Identity, case_id: str) -> Response:
    if identity.tenant_id is None:
        return refusal("identity.not_a_tenant", status=403)
    view = await get_case(gateway.services, tenant_id=identity.tenant_id, case_id=case_id)
    if view is None:
        return refusal("case.not_found", status=404)
    return JSONResponse(content=case_payload(view))


async def answer_question(
    gateway: Gateway, request: Request, identity: Identity, case_id: str
) -> Response:
    if identity.tenant_id is None:
        return refusal("identity.not_a_tenant", status=403)
    fields = as_object(await request.body())
    if fields is None:
        return refusal("request.malformed", status=400)
    answer = await ask_case(
        gateway.services,
        gateway.advisors(identity.tenant_id, case_id),
        tenant_id=identity.tenant_id,
        case_id=case_id,
        question=text_of(fields, "question"),
    )
    if answer is None:
        return refusal("case.not_found", status=404)
    return JSONResponse(content=answer_payload(answer))


async def reprocess(gateway: Gateway, identity: Identity, document_id: str) -> Response:
    if not identity.is_curator:
        return refusal("identity.not_curator", status=403)
    done = await reprocess_document(
        gateway.services, document_id=document_id, correlation_id=identity.name
    )
    if isinstance(done, ReprocessRefused):
        return refusal(done.code, status=409)
    return JSONResponse(
        status_code=202,
        content={
            "case_id": done.case_id,
            "document_id": done.document_id,
            "job_id": done.job_id,
            "options": done.options,
        },
    )


def error_response(request_id: str | None, *, code: int, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


async def message(gateway: Gateway, request: Request, identity: Identity) -> Response:
    """Subconjunto de A2A que Argos sirve: sus capacidades de texto, sin LLM de por medio."""
    fields = as_object(await request.body())
    if fields is None:
        return refusal("request.malformed", status=400)
    request_id = optional_text(fields, "id")
    method = text_of(fields, "method", "message/send")
    if method != "message/send":
        return error_response(request_id, code=-32601, message=method, status=400)
    params = object_of(fields, "params")
    skill = text_of(params, "skill")
    if skill not in {str(spec.name) for spec in GATEWAY_CAPABILITIES if spec.remote}:
        return error_response(request_id, code=-32602, message="unknown skill", status=404)
    result = await run_skill(gateway, skill, strings_in(object_of(params, "input")), identity)
    if result is None:
        return error_response(request_id, code=-32001, message="not found", status=404)
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})


async def run_skill(
    gateway: Gateway, skill: str, given: dict[str, str], identity: Identity
) -> dict[str, object] | None:
    if identity.tenant_id is None:
        return {"error": "identity.not_a_tenant"}
    tenant_id = identity.tenant_id
    services = gateway.services
    if skill == CapabilityName.ANALYZE_NOTICE:
        analysis = await analyze_notice(
            services,
            tenant_id=tenant_id,
            notice=Notice(
                text=given.get("text", ""),
                links=tuple(link for link in given.get("links", "").split(",") if link),
            ),
            correlation_id=given.get("correlation_id", identity.name),
            sleep=gateway.sleep,
        )
        if isinstance(analysis, NoticeRefused):
            return {"error": analysis.code}
        return {
            "case_id": analysis.case_id,
            "state": str(analysis.state),
            "verdict": verdict_payload(analysis.verdict),
        }
    if skill == CapabilityName.GET_CASE:
        view = await get_case(services, tenant_id=tenant_id, case_id=given.get("case_id", ""))
        return None if view is None else case_payload(view)
    if skill == CapabilityName.GET_JOB:
        job = await get_job(services, tenant_id=tenant_id, job_id=given.get("job_id", ""))
        return None if job is None else job_payload(job)
    case_id = given.get("case_id", "")
    answer = await ask_case(
        services,
        gateway.advisors(tenant_id, case_id),
        tenant_id=tenant_id,
        case_id=case_id,
        question=given.get("question", ""),
    )
    return None if answer is None else answer_payload(answer)


def control_plane_guard(gateway: Gateway) -> Callable[[Request, Dispatch], Awaitable[Response]]:
    """El plano de control de AgentOS (sesiones, trazas, agentes) es del curador."""

    async def guard(request: Request, call_next: Dispatch) -> Response:
        path = request.url.path
        if path.startswith(PUBLIC_PATHS) or path.startswith(TENANT_PREFIX):
            return await call_next(request)
        identity = resolve(bearer_token(request.headers.get("authorization")), gateway.identities)
        if identity is None:
            return refusal("identity.unknown", status=401)
        if not identity.is_curator:
            return refusal("identity.not_curator", status=403)
        return await call_next(request)

    return guard


def build_app(gateway: Gateway) -> FastAPI:
    app = FastAPI(title="Argos", version=gateway.version)

    async def card() -> JSONResponse:
        return JSONResponse(
            content=agent_card(name="Argos", version=gateway.version, url=gateway.public_url)
        )

    async def notices(request: Request) -> Response:
        return await serve(gateway, request, analyze)

    async def documents(request: Request) -> Response:
        return await serve(gateway, request, submit)

    async def jobs(request: Request, job_id: str) -> Response:
        async def handler(wired: Gateway, _: Request, identity: Identity) -> Response:
            return await show_job(wired, identity, job_id)

        return await serve(gateway, request, handler)

    async def cases(request: Request, case_id: str) -> Response:
        async def handler(wired: Gateway, _: Request, identity: Identity) -> Response:
            return await show_case(wired, identity, case_id)

        return await serve(gateway, request, handler)

    async def questions(request: Request, case_id: str) -> Response:
        async def handler(wired: Gateway, given: Request, identity: Identity) -> Response:
            return await answer_question(wired, given, identity, case_id)

        return await serve(gateway, request, handler)

    async def reprocessing(request: Request, document_id: str) -> Response:
        async def handler(wired: Gateway, _: Request, identity: Identity) -> Response:
            return await reprocess(wired, identity, document_id)

        return await serve(gateway, request, handler)

    async def messages(request: Request) -> Response:
        return await serve(gateway, request, message)

    app.add_api_route(CARD_PATH, card, methods=["GET"])
    app.add_api_route(MESSAGES_PATH, messages, methods=["POST"])
    for spec, endpoint in (
        (capability(CapabilityName.ANALYZE_NOTICE), notices),
        (capability(CapabilityName.SUBMIT_DOCUMENT), documents),
        (capability(CapabilityName.GET_JOB), jobs),
        (capability(CapabilityName.GET_CASE), cases),
        (capability(CapabilityName.ASK_CASE), questions),
        (capability(CapabilityName.REPROCESS_DOCUMENT), reprocessing),
    ):
        app.add_api_route(spec.path, endpoint, methods=[spec.method])
    app.add_middleware(BaseHTTPMiddleware, dispatch=control_plane_guard(gateway))
    return app
