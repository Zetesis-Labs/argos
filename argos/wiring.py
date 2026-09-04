"""Cableado único: los adaptadores reales que usan la CLI y el API."""

from __future__ import annotations

from dataclasses import dataclass

from agno.db.base import BaseDb
from agno.models.openai import OpenAIChat

from argos.agents.cluster import build_advisor, build_cluster
from argos.config import Settings
from argos.core.policy import Policy
from argos.core.ports import CaseAdvisor
from argos.platform.agno_db import build_agno_db
from argos.platform.clock import SystemClock
from argos.platform.ids import TimeOrderedIds
from argos.platform.ledger import SurrealLedger, ledger_for
from argos.platform.llm import build_model
from argos.usecases.deps import Services
from argos.usecases.gateway import Analysts


@dataclass(frozen=True)
class Wiring:
    services: Services
    ledger: SurrealLedger
    model: OpenAIChat
    sessions: BaseDb
    settings: Settings

    def investigators(self, case_id: str) -> Analysts:
        cluster = build_cluster(self.services, self.settings, case_id=case_id, db=self.sessions)
        return cluster.investigator, cluster.narrator

    def advisors(self, case_id: str) -> CaseAdvisor:
        return build_advisor(
            self.model, services=self.services, case_id=case_id, db=self.sessions
        )


def build_wiring(settings: Settings, policy: Policy) -> Wiring:
    ledger = ledger_for(settings, "gateway")
    return Wiring(
        services=Services(
            ledger=ledger, clock=SystemClock(), ids=TimeOrderedIds(), policy=policy
        ),
        ledger=ledger,
        model=build_model(settings, settings.analysis_model),
        sessions=build_agno_db(settings),
        settings=settings,
    )
