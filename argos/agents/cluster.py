"""Agentes de Agno: dos investigadores, el redactor y el conversacional.

Aquí no vive ninguna regla de negocio. El nivel lo calcula `core.analysis.score`
y las transiciones el caso de uso; estos agentes interpretan y redactan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.models.openai import OpenAIChat

from argos.agents.tools import tools_for
from argos.config import Settings
from argos.core.agents import INVESTIGATORS, AgentName
from argos.core.model import Analysis
from argos.core.ports import CaseBrief, ConversationBrief, Investigation, VerdictBrief
from argos.core.reports import (
    conversation_prompt,
    fallback_answer,
    fallback_summary,
    investigation_prompt,
    parse_investigation,
    verdict_prompt,
)
from argos.platform.agent import run_text
from argos.platform.llm import build_model, close_model
from argos.usecases.deps import Bookkeeping
from argos.usecases.tools import ToolCaller

# Argos es local y de un solo usuario: Agno necesita un identificador, no una identidad.
LOCAL_USER = "local"

ANALYSIS_OF_AGENT: Mapping[AgentName, Analysis] = {
    AgentName.TRIAGE: Analysis.TRIAGE,
    AgentName.PATTERNS: Analysis.PATTERNS,
}

ROLES: Mapping[AgentName, str] = {
    AgentName.TRIAGE: (
        "Interpretas de qué va el aviso, qué tipología de fraude sugiere y qué "
        "identificadores lo sostienen. No puntúas."
    ),
    AgentName.PATTERNS: (
        "Detectas técnicas de manipulación y siempre citas el fragmento literal "
        "del aviso que las sostiene."
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
    "No inventes evidencia: toda señal necesita fuente, fecha, valor y cita literal.",
    "No declares advertencias oficiales ni reincidencias: eso lo comprueba el sistema.",
    "Usa solo tus herramientas. No pides ni recibes credenciales ni consultas libres.",
)


def build_agent(
    agent: AgentName,
    *,
    model: OpenAIChat,
    services: Bookkeeping,
    case_id: str,
    db: BaseDb | None,
) -> Agent:
    caller = ToolCaller(agent=agent, case_id=case_id)
    return Agent(
        name=str(agent),
        role=ROLES[agent],
        instructions=list(COMMON_RULES),
        model=model,
        tools=[bound.call for bound in tools_for(services, caller)],
        db=db,
        store_tool_messages=False,
        telemetry=False,
    )


class SequentialInvestigator:
    """Sin equipo: cada especialista responde su contrato y el núcleo une lo sostenido."""

    def __init__(self, agents: Mapping[AgentName, Agent], *, user_id: str) -> None:
        self._agents = dict(agents)
        self._user_id = user_id

    async def investigate(self, brief: CaseBrief) -> Investigation:
        merged = Investigation(signals=(), entities=(), missing=())
        for name, agent in self._agents.items():
            answer = await run_text(
                agent,
                investigation_prompt(brief),
                user_id=self._user_id,
                session_id=f"case-{brief.case_id}",
            )
            reported = parse_investigation(answer, expected=(ANALYSIS_OF_AGENT[name],))
            merged = Investigation(
                signals=merged.signals + reported.signals,
                entities=merged.entities + reported.entities,
                missing=merged.missing + reported.missing,
            )
        return Investigation(
            signals=merged.signals,
            entities=merged.entities,
            missing=tuple(dict.fromkeys(merged.missing)),
        )


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


class AgentAdvisor:
    def __init__(self, agent: Agent, *, user_id: str) -> None:
        self._agent = agent
        self._user_id = user_id

    async def answer(self, brief: ConversationBrief) -> str:
        answered = await run_text(
            self._agent,
            conversation_prompt(brief),
            user_id=self._user_id,
            session_id=f"case-{brief.case_id}",
        )
        return answered.strip() or fallback_answer(brief)


def build_advisor(
    model: OpenAIChat, *, services: Bookkeeping, case_id: str, db: BaseDb | None = None
) -> AgentAdvisor:
    return AgentAdvisor(
        build_agent(
            AgentName.CONVERSATION, model=model, services=services, case_id=case_id, db=db
        ),
        user_id=LOCAL_USER,
    )


@dataclass(frozen=True)
class AgentCluster:
    specialists: tuple[Agent, ...]
    writer: Agent
    investigator: SequentialInvestigator
    narrator: AgentNarrator
    model: OpenAIChat

    async def close(self) -> None:
        await close_model(self.model)


def build_cluster(
    services: Bookkeeping,
    settings: Settings,
    *,
    case_id: str,
    db: BaseDb | None = None,
    members: Sequence[AgentName] = INVESTIGATORS,
) -> AgentCluster:
    model = build_model(settings, settings.analysis_model)
    specialists = {
        member: build_agent(member, model=model, services=services, case_id=case_id, db=db)
        for member in members
    }
    writer = build_agent(
        AgentName.VERDICT_WRITER, model=model, services=services, case_id=case_id, db=db
    )
    return AgentCluster(
        specialists=tuple(specialists.values()),
        writer=writer,
        investigator=SequentialInvestigator(specialists, user_id=LOCAL_USER),
        narrator=AgentNarrator(writer, user_id=LOCAL_USER),
        model=model,
    )
