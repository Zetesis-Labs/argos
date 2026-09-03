"""Clúster de Agno: especialistas, `investigation_team` y redactor (constitución §8).

Aquí no vive ninguna regla de negocio. El nivel lo calcula `core.score` y las
transiciones el workflow; estos agentes interpretan, consultan y redactan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.openai import OpenAIChat
from agno.team import Team

from argos.agents.tools import tools_for
from argos.config import Settings
from argos.core.agents import INVESTIGATION_TEAM, AgentName
from argos.core.model import Analysis
from argos.core.ports import CaseBrief, Investigation, VerdictBrief
from argos.core.reports import (
    fallback_summary,
    investigation_prompt,
    parse_investigation,
    verdict_prompt,
)
from argos.platform.agent import run_text
from argos.platform.llm import build_model, close_model
from argos.usecases.deps import Bookkeeping
from argos.usecases.tools import ToolCaller

ANALYSIS_OF_AGENT: Mapping[AgentName, Analysis] = {
    AgentName.TRIAGE: Analysis.TRIAGE,
    AgentName.REGISTRIES: Analysis.REGISTRIES,
    AgentName.DOMAIN: Analysis.DOMAIN,
    AgentName.PATTERNS: Analysis.PATTERNS,
    AgentName.MEMORY: Analysis.MEMORY,
    AgentName.DOCUMENT: Analysis.DOCUMENT,
}

ROLES: Mapping[AgentName, str] = {
    AgentName.TRIAGE: ("Normalizas identificadores del aviso y propones tipologías. No puntúas."),
    AgentName.REGISTRIES: (
        "Buscas coincidencias con advertencias oficiales vigentes, clones incluidos."
    ),
    AgentName.DOMAIN: (
        "Valoras registro, certificado, reputación y parecido con marcas de un dominio."
    ),
    AgentName.PATTERNS: (
        "Detectas técnicas de manipulación y siempre citas el fragmento que las sostiene."
    ),
    AgentName.MEMORY: (
        "Consultas reincidencias por identificador y solo manejas agregados de la memoria."
    ),
    AgentName.DOCUMENT: (
        "Lees el estado del documento, su manifiesto y sus fragmentos autorizados."
    ),
    AgentName.VERDICT_WRITER: (
        "Explicas un nivel ya calculado con sus indicios. No puedes cambiarlo."
    ),
    AgentName.CONVERSATION: (
        "Respondes dudas sobre un veredicto emitido apoyándote en su evidencia."
    ),
}

COMMON_RULES = (
    "Habla de indicios y coincidencias: nunca afirmes que algo es una estafa ni "
    "señales a una persona física.",
    "No inventes evidencia: toda señal necesita fuente, fecha, valor y cita.",
    "Usa solo tus herramientas. No pides ni recibes credenciales ni consultas libres.",
)

TEAM_NAME = "investigation_team"
TEAM_INSTRUCTIONS = (
    "Coordinas a los especialistas de investigación de un caso de posible fraude.",
    "Reparte el trabajo, reúne sus señales y responde con el JSON del contrato.",
    "No calcules ningún nivel de riesgo: eso es del núcleo determinista.",
)


def build_specialist(
    agent: AgentName,
    *,
    model: OpenAIChat,
    services: Bookkeeping,
    tenant_id: str,
    case_id: str,
    db: BaseDb | None,
) -> Agent:
    caller = ToolCaller(agent=agent, tenant_id=tenant_id, case_id=case_id)
    return Agent(
        name=str(agent),
        role=ROLES[agent],
        instructions=list(COMMON_RULES),
        model=model,
        tools=[bound.call for bound in tools_for(services, caller)],
        db=db,
        # R8: la sesión guarda referencias, no los fragmentos que viajan en las
        # respuestas de herramienta.
        store_tool_messages=False,
        telemetry=False,
    )


def build_specialists(
    members: Sequence[AgentName],
    *,
    model: OpenAIChat,
    services: Bookkeeping,
    tenant_id: str,
    case_id: str,
    db: BaseDb | None,
) -> tuple[Agent, ...]:
    return tuple(
        build_specialist(
            member, model=model, services=services, tenant_id=tenant_id, case_id=case_id, db=db
        )
        for member in members
    )


def build_team(specialists: Sequence[Agent], *, model: OpenAIChat, db: BaseDb | None) -> Team:
    return Team(
        name=TEAM_NAME,
        members=list(specialists),
        instructions=list(TEAM_INSTRUCTIONS),
        model=model,
        db=db,
        store_tool_messages=False,
        telemetry=False,
    )


def build_writer(*, model: OpenAIChat, db: BaseDb | None) -> Agent:
    return Agent(
        name=str(AgentName.VERDICT_WRITER),
        role=ROLES[AgentName.VERDICT_WRITER],
        instructions=list(COMMON_RULES),
        model=model,
        db=db,
        store_tool_messages=False,
        telemetry=False,
    )


class TeamInvestigator:
    def __init__(self, team: Team, *, expected: Sequence[Analysis], user_id: str) -> None:
        self._team = team
        self._expected = tuple(expected)
        self._user_id = user_id

    async def investigate(self, brief: CaseBrief) -> Investigation:
        answer = await run_text(
            self._team,
            investigation_prompt(brief),
            user_id=self._user_id,
            session_id=f"case-{brief.case_id}",
        )
        return parse_investigation(answer, expected=self._expected)


class AgentNarrator:
    def __init__(self, writer: Agent, *, user_id: str) -> None:
        self._writer = writer
        self._user_id = user_id

    async def narrate(self, brief: VerdictBrief) -> str:
        answer = await run_text(
            self._writer,
            verdict_prompt(brief),
            user_id=self._user_id,
            session_id=f"case-{brief.case_id}",
        )
        return answer.strip() or fallback_summary(brief)


@dataclass(frozen=True)
class AgentCluster:
    team: Team
    specialists: tuple[Agent, ...]
    writer: Agent
    investigator: TeamInvestigator
    narrator: AgentNarrator
    model: OpenAIChat

    async def close(self) -> None:
        await close_model(self.model)


def build_cluster(
    services: Bookkeeping,
    settings: Settings,
    *,
    tenant_id: str,
    case_id: str,
    db: BaseDb | None = None,
    members: Sequence[AgentName] = INVESTIGATION_TEAM,
) -> AgentCluster:
    model = build_model(settings, settings.analysis_model)
    specialists = build_specialists(
        members, model=model, services=services, tenant_id=tenant_id, case_id=case_id, db=db
    )
    team = build_team(specialists, model=model, db=db)
    writer = build_writer(model=model, db=db)
    return AgentCluster(
        team=team,
        specialists=specialists,
        writer=writer,
        investigator=TeamInvestigator(
            team,
            expected=[ANALYSIS_OF_AGENT[member] for member in members],
            user_id=tenant_id,
        ),
        narrator=AgentNarrator(writer, user_id=tenant_id),
        model=model,
    )
