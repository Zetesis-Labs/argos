"""API HTTP local. Argos corre en el devcontainer de quien lo usa y escucha en
loopback: no hay credencial que comprobar ni error que esconder."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from argos.api.payloads import as_object, optional_text, strings_of, text_of
from argos.core.capabilities import HEALTH_PATH, CapabilityName, capability
from argos.core.model import ReviewState
from argos.core.notices import Notice
from argos.core.ports import CaseAdvisor
from argos.usecases.deps import Bookkeeping
from argos.usecases.gateway import (
    AnalysisFailed,
    CaseAnswer,
    Investigators,
    analyze_notice,
    ask_case,
    review_case,
)
from argos.usecases.notices import NoticeRefused
from argos.usecases.queries import CaseView, VerdictSummary, get_case

Advisors = Callable[[str], CaseAdvisor]
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


@dataclass(frozen=True)
class Gateway:
    services: Bookkeeping
    investigators: Investigators
    advisors: Advisors
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
        "review_state": str(view.review_state),
        "error": view.error,
        "verdict": verdict_payload(view.verdict),
        "signals": [
            {
                "analysis": str(signal.analysis),
                "code": signal.code,
                "strength": str(signal.strength),
                "official": signal.official,
                "recidivism": signal.recidivism,
                "source": signal.source,
                "quote": signal.quote,
            }
            for signal in view.signals
        ],
    }


def answer_payload(answer: CaseAnswer) -> dict[str, object]:
    return {
        "case_id": answer.case_id,
        "answer": answer.answer,
        "verdict": verdict_payload(answer.view.verdict),
    }


def refusal(code: str, *, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code})


async def analyze(gateway: Gateway, request: Request) -> Response:
    fields = as_object(await request.body())
    if fields is None:
        return refusal("request.malformed", status=400)
    result = await analyze_notice(
        gateway.services,
        gateway.investigators,
        Notice(
            text=text_of(fields, "text"),
            links=strings_of(fields, "links"),
            language_hint=optional_text(fields, "language"),
        ),
    )
    if isinstance(result, NoticeRefused):
        return refusal(result.code, status=422)
    if isinstance(result, AnalysisFailed):
        return JSONResponse(
            status_code=500, content={"case_id": result.case_id, "error": result.error}
        )
    return JSONResponse(content=case_payload(result))


def build_app(gateway: Gateway, *, lifespan: Lifespan | None = None) -> FastAPI:
    app = FastAPI(title="Argos", version=gateway.version, lifespan=lifespan)

    async def health() -> JSONResponse:
        """Sano quiere decir que el libro responde, no que el proceso siga vivo."""
        try:
            await gateway.services.ledger.case("health-probe")
        except Exception as error:
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", "ledger": f"{type(error).__name__}: {error}"},
            )
        return JSONResponse(content={"status": "ok", "version": gateway.version})

    async def notices(request: Request) -> Response:
        return await analyze(gateway, request)

    async def cases(case_id: str) -> Response:
        view = await get_case(gateway.services, case_id)
        if view is None:
            return refusal("case.not_found", status=404)
        return JSONResponse(content=case_payload(view))

    async def questions(request: Request, case_id: str) -> Response:
        fields = as_object(await request.body())
        if fields is None:
            return refusal("request.malformed", status=400)
        answer = await ask_case(
            gateway.services,
            gateway.advisors(case_id),
            case_id=case_id,
            question=text_of(fields, "question"),
        )
        if answer is None:
            return refusal("case.not_found", status=404)
        return JSONResponse(content=answer_payload(answer))

    async def reviews(request: Request, case_id: str) -> Response:
        fields = as_object(await request.body())
        if fields is None:
            return refusal("request.malformed", status=400)
        try:
            review = ReviewState(text_of(fields, "review"))
        except ValueError:
            return refusal("review.unknown", status=422)
        marked = await review_case(gateway.services, case_id=case_id, review=review)
        if marked is None:
            return refusal("case.not_found", status=404)
        return JSONResponse(content={"case_id": marked.id, "review_state": str(review)})

    app.add_api_route(HEALTH_PATH, health, methods=["GET"])
    for spec, endpoint in (
        (capability(CapabilityName.ANALYZE_NOTICE), notices),
        (capability(CapabilityName.GET_CASE), cases),
        (capability(CapabilityName.ASK_CASE), questions),
        (capability(CapabilityName.REVIEW_CASE), reviews),
    ):
        app.add_api_route(spec.path, endpoint, methods=[spec.method])
    return app
