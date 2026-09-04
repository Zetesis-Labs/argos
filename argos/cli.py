"""`argos` en la terminal: la superficie principal de un producto local.

`argos "texto del aviso"` analiza y escribe el veredicto con su evidencia.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from argos.config import Settings
from argos.core.model import ReviewState, RiskLevel
from argos.core.notices import Notice
from argos.core.policy import Policy
from argos.usecases.gateway import AnalysisFailed, analyze_notice, ask_case, review_case
from argos.usecases.notices import NoticeRefused
from argos.usecases.queries import CaseView, get_case
from argos.wiring import Wiring, build_wiring

MARKS: dict[RiskLevel, str] = {
    RiskLevel.CRITICAL: "CRÍTICO",
    RiskLevel.HIGH: "ALTO",
    RiskLevel.MEDIUM: "MEDIO",
    RiskLevel.LOW: "BAJO",
    RiskLevel.UNDETERMINED: "SIN DETERMINAR",
}


def render(view: CaseView) -> str:
    lines = [f"caso {view.id}  ·  {view.state}"]
    if view.error is not None:
        lines.append(f"error: {view.error}")
    verdict = view.verdict
    if verdict is not None:
        lines.extend(("", f"{MARKS[verdict.level]}", "", verdict.summary))
        if view.signals:
            lines.append("")
            lines.append("Indicios:")
            for signal in view.signals:
                mark = " ·oficial" if signal.official else (" ·previo" if signal.recidivism else "")
                lines.append(f"  [{signal.analysis}/{signal.code}{mark}] {signal.quote}")
                lines.append(f"      fuente: {signal.source}")
        else:
            lines.extend(("", "Sin indicios sostenidos por evidencia."))
        lines.append("")
        lines.append("Qué hacer:")
        lines.extend(f"  - {action}" for action in verdict.actions)
        if verdict.missing:
            lines.append("")
            lines.append(f"No se pudo completar: {', '.join(verdict.missing)}.")
    return "\n".join(lines)


async def run_analyze(wiring: Wiring, text: str, links: Sequence[str]) -> int:
    result = await analyze_notice(
        wiring.services, wiring.investigators, Notice(text=text, links=tuple(links))
    )
    if isinstance(result, NoticeRefused):
        sys.stderr.write(f"aviso rechazado: {result.code}\n")
        return 2
    if isinstance(result, AnalysisFailed):
        sys.stderr.write(f"caso {result.case_id} falló: {result.error}\n")
        return 1
    sys.stdout.write(render(result) + "\n")
    return 0


async def run_show(wiring: Wiring, case_id: str) -> int:
    view = await get_case(wiring.services, case_id)
    if view is None:
        sys.stderr.write(f"no existe el caso {case_id}\n")
        return 1
    sys.stdout.write(render(view) + "\n")
    return 0


async def run_ask(wiring: Wiring, case_id: str, question: str) -> int:
    answer = await ask_case(
        wiring.services, wiring.advisors(case_id), case_id=case_id, question=question
    )
    if answer is None:
        sys.stderr.write(f"no existe el caso {case_id}\n")
        return 1
    sys.stdout.write(answer.answer + "\n")
    return 0


async def run_review(wiring: Wiring, case_id: str, review: str) -> int:
    try:
        marked = ReviewState(review)
    except ValueError:
        sys.stderr.write(f"revisión desconocida: {review}\n")
        return 2
    case = await review_case(wiring.services, case_id=case_id, review=marked)
    if case is None:
        sys.stderr.write(f"no existe el caso {case_id} o está analizándose\n")
        return 1
    sys.stdout.write(f"caso {case.id} marcado como {marked}\n")
    return 0


def parser() -> argparse.ArgumentParser:
    parsed = argparse.ArgumentParser(prog="argos", description="Segunda opinión ante un fraude")
    commands = parsed.add_subparsers(dest="command", required=True)

    analyze = commands.add_parser("analyze", help="analiza un aviso y escribe su veredicto")
    analyze.add_argument("text", help="texto del aviso")
    analyze.add_argument("--link", action="append", default=[], help="enlace citado en el aviso")

    show = commands.add_parser("show", help="muestra un caso ya analizado")
    show.add_argument("case_id")

    ask = commands.add_parser("ask", help="pregunta sobre un veredicto emitido")
    ask.add_argument("case_id")
    ask.add_argument("question")

    review = commands.add_parser("review", help="marca un caso confirmado o falso positivo")
    review.add_argument("case_id")
    review.add_argument("review", choices=[str(state) for state in ReviewState])
    return parsed


async def dispatch(wiring: Wiring, options: argparse.Namespace) -> int:
    command = str(options.command)
    if command == "analyze":
        return await run_analyze(wiring, str(options.text), [str(x) for x in options.link])
    if command == "show":
        return await run_show(wiring, str(options.case_id))
    if command == "ask":
        return await run_ask(wiring, str(options.case_id), str(options.question))
    return await run_review(wiring, str(options.case_id), str(options.review))


async def run(options: argparse.Namespace) -> int:
    wiring = build_wiring(Settings(), Policy())
    await wiring.ledger.connect()
    try:
        return await dispatch(wiring, options)
    finally:
        await wiring.ledger.close()


def main() -> None:
    sys.exit(asyncio.run(run(parser().parse_args())))
