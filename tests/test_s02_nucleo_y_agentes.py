"""Casos S02: caso, memoria, señales por código, agentes, CLI y API local."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from argos.agents.cluster import build_cluster
from argos.agents.tools import dumps, tools_for
from argos.api.gateway import Gateway, build_app, case_payload
from argos.cli import parser, render, run_analyze
from argos.config import WORKLOADS, Settings
from argos.core.agents import AgentName, Capability, capabilities_of
from argos.core.analysis import (
    ACTIONS,
    DraftSignal,
    EntityHistory,
    Evidence,
    assess,
    score,
    usable,
)
from argos.core.identifiers import extract_identifiers, iban_is_valid, registrable_domain
from argos.core.knowledge import parse_knowledge_bundle, warnings_from_bundle
from argos.core.model import (
    Analysis,
    Case,
    CaseState,
    EntityKind,
    Insert,
    OfficialWarning,
    ReviewState,
    RiskLevel,
    Strength,
    Update,
    VerdictOutcome,
    entity_id,
)
from argos.core.notices import Notice
from argos.core.policy import AnalysisPolicy, Policy
from argos.core.ports import ConversationBrief, Investigation, Investigator, Ledger
from argos.core.reports import NO_VERDICT_YET, investigation_prompt, parse_investigation
from argos.devtools.bootstrap_db import SCHEMA_VERSION, apply_schema
from argos.devtools.project_knowledge import read_bundle
from argos.platform.agno_db import build_agno_db
from argos.platform.ledger import ledger_for
from argos.platform.surreal import SurrealError, SurrealHttp
from argos.tools.fakes import (
    FakeClock,
    InMemoryLedger,
    ScriptedInvestigator,
    ScriptedNarrator,
    SequentialIds,
)
from argos.usecases.analysis import (
    BUDGET_EXHAUSTED,
    Analyzed,
    Failed,
    Skipped,
    analyze_case,
    build_brief,
    grounded,
)
from argos.usecases.deps import Services
from argos.usecases.gateway import Analysts, analyze_notice, review_case
from argos.usecases.notices import NoticeRefused, open_notice_case
from argos.usecases.queries import CaseView, get_case
from argos.usecases.signals import OFFICIAL_WARNING, PRIOR_CONFIRMED_CASE
from argos.usecases.tools import (
    CASE_NOT_FOUND,
    NOT_AUTHORIZED,
    CaseContext,
    ToolCaller,
    ToolDenied,
    find_entity_history,
    find_registry_matches,
    get_case_context,
)
from tests.support import names_in

pytestmark = pytest.mark.anyio

MEMORY_TABLES = {"entity", "entity_link", "case_entity", "warning", "signal", "verdict"}
CASE_OWNED = {"case_entity", "signal", "verdict"}
RETIRED_TABLES = {
    "tenant",
    "artifact",
    "document",
    "job",
    "attempt",
    "outbox_entry",
    "extraction",
    "chunk",
}
TEST_POLICY = Policy(analysis=AnalysisPolicy(budget=timedelta(seconds=5)))
DEMO_DOMAIN = "example-broker.test"


@pytest.fixture(scope="session")
async def ledger_schema(anyio_backend: str, settings: Settings) -> None:
    await apply_schema(settings)


@pytest.fixture(params=["surreal", "memory"])
async def ledger(
    request: pytest.FixtureRequest, anyio_backend: str, settings: Settings, ledger_schema: None
) -> AsyncIterator[Ledger]:
    if request.param == "memory":
        yield InMemoryLedger()
        return
    surreal = ledger_for(settings, "gateway")
    await surreal.connect()
    try:
        yield surreal
    finally:
        await surreal.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def services(ledger: Ledger, clock: FakeClock) -> Services:
    return Services(
        ledger=ledger,
        clock=clock,
        ids=SequentialIds(prefix=f"{uuid4().hex[:8]}-"),
        policy=TEST_POLICY,
    )


def evidence_of(clock: FakeClock, quote: str = "rentabilidad garantizada") -> Evidence:
    return Evidence(
        source="https://fuente.example/aviso",
        observed_at=clock.now(),
        value="nexolabs.test",
        quote=quote,
    )


def signal_of(
    clock: FakeClock,
    analysis: Analysis,
    strength: Strength = Strength.STRONG,
    *,
    evidence: Evidence | None = None,
    official: bool = False,
    recidivism: bool = False,
) -> DraftSignal:
    return DraftSignal(
        analysis=analysis,
        code="prueba",
        strength=strength,
        evidence=evidence or evidence_of(clock),
        official=official,
        recidivism=recidivism,
    )


def notice_of(text: str, links: tuple[str, ...] = ()) -> Notice:
    return Notice(text=text, links=links, language_hint="es")


async def opened_case(services: Services, notice: Notice) -> Case:
    opened = await open_notice_case(services, notice)
    assert isinstance(opened, Case)
    return opened


def caller_of(agent: AgentName, case_id: str) -> ToolCaller:
    return ToolCaller(agent=agent, case_id=case_id)


def analysts(investigation: Investigation, summary: str = "Resumen con indicios.") -> Analysts:
    return (ScriptedInvestigator(investigation), ScriptedNarrator(summary))


EMPTY = Investigation(signals=(), entities=(), missing=())


async def test_notice_opens_case_with_its_identifiers(services: Services) -> None:
    """S02.11 el aviso deja caso e identificadores en una transacción y aplica R1."""
    notice = notice_of(f"Escribe a soporte@{DEMO_DOMAIN} o llama al +34600111222")
    case = await opened_case(services, notice)

    assert case.state is CaseState.RECEIVED and case.notice_text == notice.text
    links = [link.entity_id for link in await services.ledger.entities_of_case(case.id)]
    assert entity_id(EntityKind.DOMAIN, DEMO_DOMAIN) in links
    assert entity_id(EntityKind.EMAIL, f"soporte@{DEMO_DOMAIN}") in links
    assert entity_id(EntityKind.PHONE, "+34600111222") in links

    again = await opened_case(services, notice)
    assert again.id != case.id

    too_long = await open_notice_case(
        services, notice_of("x" * (TEST_POLICY.notices.max_text_chars + 1))
    )
    assert too_long == NoticeRefused("notice.text_too_long")
    assert await open_notice_case(services, Notice(text="  ")) == NoticeRefused("notice.empty")
    await services.ledger.delete_case(case.id)
    await services.ledger.delete_case(again.id)


async def test_schema_is_idempotent(settings: Settings, ledger_schema: None) -> None:
    """S02.27 el esquema de casos, memoria, señales y veredictos es idempotente."""
    await apply_schema(settings)
    await apply_schema(settings)
    http = SurrealHttp(settings.surreal_url)
    info = await http.sql(
        "INFO FOR DB;", auth=settings.root_auth, ns=settings.ops_namespace, db=settings.ops_database
    )
    database = info[-1].result
    assert isinstance(database, dict)
    tables = names_in(database.get("tables"))
    assert tables >= MEMORY_TABLES | {"case"}
    assert not tables & RETIRED_TABLES

    async def fields_of(table: str) -> set[str]:
        described = await http.sql(
            f"INFO FOR TABLE {table};",
            auth=settings.root_auth,
            ns=settings.ops_namespace,
            db=settings.ops_database,
        )
        definition = described[-1].result
        assert isinstance(definition, dict)
        return names_in(definition.get("fields"))

    for table in sorted(MEMORY_TABLES | {"case"}):
        assert "tenant_id" not in await fields_of(table)
        assert ("case_id" in await fields_of(table)) is (table in CASE_OWNED)
    assert {"review_state", "notice_text", "notice_links", "error"} <= await fields_of("case")
    assert "expires_at" not in await fields_of("case")

    version = await http.sql(
        "SELECT version FROM schema_version:current;",
        auth=settings.root_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
    )
    rows = version[-1].result
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    assert rows[0].get("version") == SCHEMA_VERSION


def test_risk_levels_follow_r4(clock: FakeClock) -> None:
    """S02.28 el núcleo determinista calcula el nivel conforme a R4."""
    strong_registry = signal_of(clock, Analysis.REGISTRIES)
    strong_patterns = signal_of(clock, Analysis.PATTERNS)
    weak = signal_of(clock, Analysis.PATTERNS, Strength.WEAK)

    assert score([signal_of(clock, Analysis.REGISTRIES, official=True)], degraded=False) is (
        RiskLevel.CRITICAL
    )
    assert score([signal_of(clock, Analysis.MEMORY, recidivism=True)], degraded=False) is (
        RiskLevel.CRITICAL
    )
    assert score([strong_registry, strong_patterns], degraded=False) is RiskLevel.HIGH
    assert score([strong_registry, strong_registry], degraded=False) is RiskLevel.MEDIUM
    assert score([strong_registry], degraded=False) is RiskLevel.MEDIUM
    assert score([weak, weak, weak], degraded=False) is RiskLevel.MEDIUM
    assert score([weak, weak], degraded=False) is RiskLevel.LOW
    assert score([], degraded=False) is RiskLevel.LOW


def test_signals_without_evidence_do_not_score(clock: FakeClock) -> None:
    """S02.29 una señal sin evidencia no puntúa y un parcial nunca es low."""
    complete = evidence_of(clock)
    incomplete = (
        replace(complete, source=" "),
        replace(complete, observed_at=None),
        replace(complete, quote=""),
    )
    unsupported = [signal_of(clock, Analysis.PATTERNS, evidence=broken) for broken in incomplete]
    assert usable(unsupported) == ()

    weak = [signal_of(clock, Analysis.PATTERNS, Strength.WEAK) for _ in range(2)]
    assert assess(weak, missing=(), analyzable=True).level is RiskLevel.LOW
    degraded = assess(weak, missing=("patterns",), analyzable=True)
    assert degraded.level is RiskLevel.MEDIUM
    assert degraded.outcome is VerdictOutcome.PARTIAL and degraded.missing == ("patterns",)

    empty = assess(unsupported, missing=("triage",), analyzable=True)
    assert empty.level is RiskLevel.UNDETERMINED and empty.outcome is VerdictOutcome.PARTIAL
    assert all(ACTIONS[level] for level in RiskLevel)
    assert empty.actions


def test_agents_only_declare_read_tools(services: Services) -> None:
    """S02.30 cada agente declara solo sus herramientas y ninguna escribe en el libro."""
    assert set(AgentName) == {
        AgentName.TRIAGE,
        AgentName.PATTERNS,
        AgentName.VERDICT_WRITER,
        AgentName.CONVERSATION,
    }
    for agent in AgentName:
        bound = tools_for(services, caller_of(agent, "case-1"))
        assert {tool.capability for tool in bound} == capabilities_of(agent)
    assert set(Capability) == {
        Capability.GET_CASE_CONTEXT,
        Capability.FIND_REGISTRY_MATCHES,
        Capability.FIND_ENTITY_HISTORY,
    }


async def test_tools_refuse_the_agent_without_capability_and_the_unknown_case(
    services: Services,
) -> None:
    """S02.31 una herramienta rechaza al agente sin capacidad y al caso inexistente."""
    case = await opened_case(services, notice_of("Un aviso cualquiera"))

    writer = await get_case_context(services, caller_of(AgentName.VERDICT_WRITER, case.id))
    unknown = await get_case_context(services, caller_of(AgentName.TRIAGE, "case:ghost"))
    allowed = await get_case_context(services, caller_of(AgentName.TRIAGE, case.id))

    assert writer == ToolDenied(NOT_AUTHORIZED)
    assert unknown == ToolDenied(CASE_NOT_FOUND)
    assert isinstance(allowed, CaseContext) and allowed.case_id == case.id
    await services.ledger.delete_case(case.id)


async def test_entity_history_only_returns_aggregates(services: Services) -> None:
    """S02.33 find_entity_history devuelve agregados de la memoria, no los casos."""
    domain = f"inversiones-{uuid4().hex[:10]}.test"
    confirmed = await opened_case(services, notice_of(f"Confirmado sobre {domain}"))
    plain = await opened_case(services, notice_of(f"Otro aviso sobre {domain}"))
    await services.ledger.commit([reviewed(confirmed, ReviewState.CONFIRMED, services.clock.now())])

    history = await find_entity_history(
        services,
        caller_of(AgentName.CONVERSATION, plain.id),
        kind=EntityKind.DOMAIN,
        value=f"  {domain.upper()} ",
    )
    assert isinstance(history, EntityHistory)
    assert (history.cases, history.confirmed) == (2, True)
    assert history.first_seen_at is not None and history.last_seen_at is not None

    unknown = await find_entity_history(
        services,
        caller_of(AgentName.CONVERSATION, plain.id),
        kind=EntityKind.DOMAIN,
        value="nadie-lo-ha-visto.test",
    )
    assert isinstance(unknown, EntityHistory) and unknown.cases == 0
    await services.ledger.delete_case(confirmed.id)
    await services.ledger.delete_case(plain.id)


def reviewed(case: Case, review: ReviewState, now: datetime) -> Update:
    return Update(replace(case, review_state=review, reviewed_at=now, revision=case.revision + 1))


async def test_analysis_moves_the_case_and_issues_the_verdict(
    services: Services, clock: FakeClock
) -> None:
    """S02.35 el análisis pasa el caso a analyzing y lo cierra con su veredicto versionado."""
    text = "Rentabilidad garantizada del 40% en nexolabs.test"
    case = await opened_case(services, notice_of(text))
    investigation = Investigation(
        signals=(
            signal_of(
                clock,
                Analysis.PATTERNS,
                evidence=evidence_of(clock, quote="Rentabilidad garantizada del 40%"),
            ),
        ),
        entities=(),
        missing=(),
    )
    investigator, narrator = analysts(investigation)
    analyzed = await analyze_case(services, investigator, narrator, case_id=case.id)

    assert isinstance(analyzed, Analyzed)
    assert analyzed.case.state is CaseState.VERDICT_ISSUED
    assert analyzed.verdict.version == 1 and analyzed.verdict.level is RiskLevel.MEDIUM
    assert analyzed.verdict.summary == "Resumen con indicios."
    assert len(await services.ledger.signals_of_case(case.id)) == 1
    await services.ledger.delete_case(case.id)


async def test_a_notice_without_analyzable_content_is_insufficient(services: Services) -> None:
    """S02.36 un aviso sin entrada analizable termina insufficient y nunca en un nivel."""
    case = await opened_case(services, Notice(text="", image=b"\x89PNG\r\n\x1a\n" + b"0"))
    investigator, narrator = analysts(EMPTY)
    analyzed = await analyze_case(services, investigator, narrator, case_id=case.id)

    assert isinstance(analyzed, Analyzed)
    assert analyzed.case.state is CaseState.INSUFFICIENT
    assert analyzed.verdict.outcome is VerdictOutcome.INSUFFICIENT
    assert analyzed.verdict.level is RiskLevel.UNDETERMINED
    assert analyzed.verdict.actions
    await services.ledger.delete_case(case.id)


@pytest.fixture
async def surreal_services(
    anyio_backend: str, settings: Settings, ledger_schema: None, clock: FakeClock
) -> AsyncIterator[Services]:
    surreal = ledger_for(settings, "gateway")
    await surreal.connect()
    try:
        yield Services(
            ledger=surreal, clock=clock, ids=SequentialIds(prefix="s-"), policy=TEST_POLICY
        )
    finally:
        await surreal.close()


async def agno_session_dump(settings: Settings) -> str:
    http = SurrealHttp(settings.surreal_url)
    dumped = await http.sql(
        "SELECT * FROM agno_sessions;",
        auth=settings.root_auth,
        ns=settings.agno_namespace,
        db=settings.agno_database,
    )
    return json.dumps([result.result for result in dumped], default=str, ensure_ascii=False)


async def test_real_cluster_analyses_without_leaking_the_notice(
    settings: Settings, surreal_services: Services
) -> None:
    """S02.37 el clúster real analiza con el modelo mock sin dejar el aviso en la sesión."""
    services = surreal_services
    marker = uuid4().hex
    case = await opened_case(services, notice_of(f"aviso sintetico {marker}"))
    try:
        cluster = build_cluster(services, settings, case_id=case.id, db=build_agno_db(settings))
        try:
            analyzed = await analyze_case(
                services, cluster.investigator, cluster.narrator, case_id=case.id
            )
        finally:
            await cluster.close()

        assert isinstance(analyzed, Analyzed)
        assert analyzed.case.state in (CaseState.VERDICT_ISSUED, CaseState.PARTIAL)
        assert analyzed.verdict.actions
        assert not await services.ledger.signals_of_case(case.id)
        assert all(agent.store_tool_messages is False for agent in cluster.specialists)
        assert marker in investigation_prompt(build_brief(case))
        assert marker not in await agno_session_dump(settings)
    finally:
        await services.ledger.delete_case(case.id)


class ScriptedAdvisor:
    def __init__(self, answer: str = "El nivel no cambia; revisa las acciones.") -> None:
        self._answer = answer
        self.briefs: list[ConversationBrief] = []

    async def answer(self, brief: ConversationBrief) -> str:
        self.briefs.append(brief)
        return self._answer


@pytest.fixture
def advisor() -> ScriptedAdvisor:
    return ScriptedAdvisor()


@pytest.fixture
def investigation() -> Investigation:
    return EMPTY


@pytest.fixture
def gateway_app(
    services: Services, advisor: ScriptedAdvisor, investigation: Investigation
) -> FastAPI:
    return build_app(
        Gateway(
            services=services,
            investigators=lambda case_id: analysts(investigation),
            advisors=lambda case_id: advisor,
            version="test",
        )
    )


@pytest.fixture
async def client(anyio_backend: str, gateway_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app), base_url="http://argos.test"
    ) as running:
        yield running


async def test_analyze_notice_returns_the_verdict_in_the_same_call(
    services: Services, clock: FakeClock
) -> None:
    """S02.40 analyze_notice devuelve el veredicto y su evidencia en la misma llamada."""
    investigation = Investigation(
        signals=(
            signal_of(
                clock, Analysis.PATTERNS, evidence=evidence_of(clock, quote="dobla tu dinero")
            ),
        ),
        entities=(),
        missing=(),
    )
    view = await analyze_notice(
        services,
        lambda case_id: analysts(investigation),
        notice_of("Invierte hoy y dobla tu dinero en una semana"),
    )
    assert isinstance(view, CaseView)
    assert view.state is CaseState.VERDICT_ISSUED
    assert view.verdict is not None and view.verdict.version == 1
    assert [signal.quote for signal in view.signals] == ["dobla tu dinero"]
    await services.ledger.delete_case(view.id)


async def test_the_api_serves_the_case_and_refuses_the_unknown(
    client: httpx.AsyncClient, services: Services
) -> None:
    """S02.42 la API local sirve el caso con su evidencia y responde 404 al inexistente."""
    accepted = await client.post("/v1/notices", json={"text": "Un aviso propio"})
    assert accepted.status_code == 200
    case_id = str(accepted.json()["case_id"])
    assert "signals" in accepted.json()

    served = await client.get(f"/v1/cases/{case_id}")
    assert served.status_code == 200 and served.json()["case_id"] == case_id
    missing = await client.get("/v1/cases/no-existe")
    question = await client.post("/v1/cases/no-existe/questions", json={"question": "¿qué?"})
    assert [missing.status_code, question.status_code] == [404, 404]
    assert missing.json() == {"error": "case.not_found"}
    assert (await client.get("/health")).json()["status"] == "ok"
    await services.ledger.delete_case(case_id)


async def test_ask_case_answers_without_touching_the_verdict(
    client: httpx.AsyncClient, services: Services, advisor: ScriptedAdvisor
) -> None:
    """S02.43 ask_case responde con la evidencia persistida y no muta el veredicto."""
    accepted = await client.post(
        "/v1/notices", json={"text": f"Promesa de rentabilidad en {DEMO_DOMAIN}"}
    )
    case_id = str(accepted.json()["case_id"])
    before = await services.ledger.current_verdict(case_id)
    assert before is not None

    answered = await client.post(
        f"/v1/cases/{case_id}/questions", json={"question": "¿Puedo recuperar el dinero?"}
    )
    assert answered.status_code == 200
    assert answered.json()["answer"] == "El nivel no cambia; revisa las acciones."
    assert advisor.briefs[0].level is before.level

    after = await services.ledger.current_verdict(case_id)
    assert after is not None and after.version == before.version

    pending = await opened_case(services, notice_of("Aviso todavía sin analizar"))
    unanswered = await client.post(
        f"/v1/cases/{pending.id}/questions", json={"question": "¿ya está?"}
    )
    assert unanswered.status_code == 200
    assert unanswered.json()["answer"] == NO_VERDICT_YET
    assert unanswered.json()["verdict"] is None
    await services.ledger.delete_case(case_id)
    await services.ledger.delete_case(pending.id)


async def test_a_settled_case_is_not_analysed_twice(services: Services) -> None:
    """S02.45 un caso ya cerrado no se vuelve a analizar ni duplica su veredicto."""
    case = await opened_case(services, notice_of("Aviso con un enlace a banco.test"))
    investigator, narrator = analysts(EMPTY)
    first = await analyze_case(services, investigator, narrator, case_id=case.id)
    assert isinstance(first, Analyzed)

    again = await analyze_case(services, investigator, narrator, case_id=case.id)
    assert isinstance(again, Skipped)
    current = await services.ledger.current_verdict(case.id)
    assert current is not None and current.version == 1
    await services.ledger.delete_case(case.id)


async def test_the_agent_user_can_only_read(settings: Settings, ledger_schema: None) -> None:
    """S02.47 el proceso entra con su identidad y la de los agentes es de solo lectura."""
    await apply_schema(settings)
    await apply_schema(settings)
    http = SurrealHttp(settings.surreal_url)
    info = await http.sql(
        "INFO FOR DB;", auth=settings.root_auth, ns=settings.ops_namespace, db=settings.ops_database
    )
    database = info[-1].result
    assert isinstance(database, dict)
    users = names_in(database.get("users"))
    assert set(WORKLOADS) <= users
    assert not users & {"ledger", "dispatcher", "resumer", "analyzer", "worker", "janitor"}

    credentials = settings.workload("gateway")
    assert await http.sign_in(
        ns=settings.ops_namespace,
        db=settings.ops_database,
        user=credentials.user,
        password=credentials.password.get_secret_value(),
    )
    with pytest.raises(SurrealError):
        await http.sign_in(
            ns=settings.ops_namespace,
            db=settings.ops_database,
            user=credentials.user,
            password="contraseña incorrecta",
        )

    agent_auth = await http.sign_in(
        ns=settings.ops_namespace,
        db=settings.ops_database,
        user=settings.surreal_agent_user,
        password=settings.surreal_agent_password.get_secret_value(),
    )
    readable = await http.sql(
        "SELECT version FROM schema_version:current;",
        auth=agent_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
    )
    assert readable[-1].result
    intruder = f"intruso{uuid4().hex[:8]}"
    await http.sql(
        f"CREATE case:{intruder} SET state = 'received', revision = 0;",
        auth=agent_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
        raise_on_error=False,
    )
    written = await http.sql(
        f"SELECT * FROM case:{intruder};",
        auth=settings.root_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
    )
    assert written[-1].result == []
    escalation = await http.sql(
        "DEFINE USER hacker ON DATABASE PASSWORD 'x' ROLES OWNER;",
        auth=agent_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
        raise_on_error=False,
    )
    assert not escalation[-1].ok
    assert "Not enough permissions" in str(escalation[-1].result)


class BrokenInvestigator:
    async def investigate(self, brief: object) -> Investigation:
        raise RuntimeError("el modelo no responde")


async def test_a_failed_analysis_leaves_the_case_operable(
    client: httpx.AsyncClient, services: Services
) -> None:
    """S02.51 un análisis que falla deja el caso failed con su error y lo dice al llamante."""
    case = await opened_case(services, notice_of("promesa de rentabilidad garantizada"))
    broken: Investigator = BrokenInvestigator()
    outcome = await analyze_case(services, broken, ScriptedNarrator(), case_id=case.id)

    assert isinstance(outcome, Failed)
    assert "el modelo no responde" in outcome.error
    stored = await services.ledger.case(case.id)
    assert stored is not None and stored.state is CaseState.FAILED
    assert stored.error is not None and "RuntimeError" in stored.error

    view = await get_case(services, case.id)
    assert isinstance(view, CaseView)
    rendered = dumps(case_payload(view))
    assert "el modelo no responde" in rendered

    refused = await client.post("/v1/notices", json={"text": "x" * 20_001})
    assert refused.status_code == 422 and refused.json() == {"error": "notice.text_too_long"}
    await services.ledger.delete_case(case.id)


async def test_an_analysis_that_exhausts_its_budget_fails(services: Services) -> None:
    """S02.62 un análisis que agota el presupuesto de R15 termina failed, no colgado."""

    class SlowInvestigator:
        async def investigate(self, brief: object) -> Investigation:
            import asyncio

            await asyncio.sleep(3)
            return EMPTY

    impatient = replace(services, policy=Policy(analysis=AnalysisPolicy(budget=timedelta(seconds=0.1))))
    case = await opened_case(impatient, notice_of("Un aviso que tarda demasiado"))
    slow: Investigator = SlowInvestigator()
    outcome = await analyze_case(impatient, slow, ScriptedNarrator(), case_id=case.id)

    assert isinstance(outcome, Failed) and outcome.error == BUDGET_EXHAUSTED
    stored = await impatient.ledger.case(case.id)
    assert stored is not None and stored.state is CaseState.FAILED
    await impatient.ledger.delete_case(case.id)


async def test_demo_warnings_are_queryable(settings: Settings) -> None:
    """S02.54 el catálogo sintético demuestra la consulta de registros."""
    ledger = InMemoryLedger()
    bundle = parse_knowledge_bundle(read_bundle(settings.knowledge_graph_path))
    await ledger.commit([Insert(warning) for warning in warnings_from_bundle(bundle)])
    services = Services(ledger=ledger, clock=FakeClock(), ids=SequentialIds(), policy=TEST_POLICY)
    caller = caller_of(AgentName.TRIAGE, "case-demo")

    matches = await find_registry_matches(
        services, caller, kind=EntityKind.DOMAIN, value=DEMO_DOMAIN
    )
    assert not isinstance(matches, ToolDenied)
    assert [(match.regulator, match.url, match.captured_at, match.active) for match in matches] == [
        (
            "FCA",
            "https://warnings.fca.example/demo/example-broker",
            "2026-09-01T00:00:00+00:00",
            True,
        )
    ]
    withdrawn = await find_registry_matches(
        services, caller, kind=EntityKind.DOMAIN, value="retired-platform.test"
    )
    assert not isinstance(withdrawn, ToolDenied)
    assert [match.active for match in withdrawn] == [False]


def test_services_profile_bootstraps_and_runs_argos(settings: Settings) -> None:
    """S02.55 el perfil services prepara y ejecuta Argos sin pasos manuales."""
    compose = Path(".devcontainer/docker-compose.yml").read_text(encoding="utf-8")

    def service_block(name: str) -> str:
        match = re.search(
            rf"^  {re.escape(name)}:\n(?P<body>(?:(?:    .*)?\n)*)", compose, re.MULTILINE
        )
        assert match is not None, f"falta el servicio {name}"
        return match.group(0)

    bootstrap = service_block("bootstrap")
    assert 'profiles: ["services"]' in bootstrap
    assert 'command: ["uv", "run", "--frozen", "bootstrap-local"]' in bootstrap

    gateway = service_block("gateway")
    assert 'profiles: ["services"]' in gateway
    assert 'command: ["uv", "run", "--frozen", "gateway"]' in gateway
    assert "condition: service_completed_successfully" in gateway
    assert '"127.0.0.1:${GATEWAY_PORT:-7777}:7777"' in gateway
    assert '"127.0.0.1:${GATEWAY_PORT:-7777}:7777"' not in service_block("app")
    for retired in ("dispatcher", "worker", "resumer", "analyzer", "janitor", "nats", "rustfs"):
        assert not re.search(rf"^  {retired}:\n", compose, re.MULTILINE)

    devcontainer = json.loads(Path(".devcontainer/devcontainer.json").read_text(encoding="utf-8"))
    assert set(devcontainer["runServices"]) == {"app", "surrealdb-test"}
    assert "postCreateCommand" not in devcontainer
    assert settings.surreal_url == "http://surrealdb-test:8000"


def test_identifiers_are_extracted_by_code() -> None:
    """S02.56 los identificadores del aviso se extraen y normalizan sin modelo."""
    text = (
        "Ingresa en ES9121000418450200051332 desde https://Pagos.Banco-Falso.test/alta, "
        "escribe a Soporte@Banco-Falso.test o a @soporte_falso, y usa la wallet "
        "0x52908400098527886E0F7030069857D2E4169EE7. Tel: +34 600 111 222. "
        "IBAN falso: ES0000000000000000000000."
    )
    found = {(entity.kind, entity.value) for entity in extract_identifiers(text)}

    assert (EntityKind.IBAN, "ES9121000418450200051332") in found
    assert (EntityKind.DOMAIN, "banco-falso.test") in found
    assert (EntityKind.EMAIL, "soporte@banco-falso.test") in found
    assert (EntityKind.HANDLE, "soporte_falso") in found
    assert (EntityKind.PHONE, "+34600111222") in found
    assert (EntityKind.WALLET, "0x52908400098527886E0F7030069857D2E4169EE7") in found
    assert not any(value == "ES0000000000000000000000" for _, value in found)
    assert not iban_is_valid("ES0000000000000000000000")
    assert registrable_domain("pagos.banco.co.uk") == "banco.co.uk"
    assert [entity.value for entity in extract_identifiers("", ("https://otro.test/x",))] == [
        "otro.test"
    ]


async def test_only_code_marks_official_warnings(services: Services, clock: FakeClock) -> None:
    """S02.57 la advertencia oficial y la reincidencia solo las marca el código."""
    reported = parse_investigation(
        json.dumps(
            {
                "signals": [
                    {
                        "analysis": "registries",
                        "code": "inventado",
                        "strength": "strong",
                        "source": "https://fuente.example",
                        "observed_at": clock.now().isoformat(),
                        "value": DEMO_DOMAIN,
                        "quote": "cita",
                        "official": True,
                        "recidivism": True,
                    }
                ]
            }
        ),
        expected=(Analysis.TRIAGE,),
    )
    assert reported.signals == ()

    advertised = f"chiringuito-{uuid4().hex[:10]}.test"
    await services.ledger.commit(
        [
            Insert(
                OfficialWarning(
                    id=f"w-{uuid4().hex[:10]}",
                    regulator="CNMV",
                    url="https://warnings.cnmv.example/demo",
                    entity_kind=EntityKind.DOMAIN,
                    entity_value=advertised,
                    active=True,
                    captured_at=clock.now(),
                    revision=0,
                )
            )
        ]
    )
    case = await opened_case(services, notice_of(f"Opera con {advertised} sin riesgo"))
    investigator, narrator = analysts(EMPTY)
    analyzed = await analyze_case(services, investigator, narrator, case_id=case.id)

    assert isinstance(analyzed, Analyzed)
    assert analyzed.verdict.level is RiskLevel.CRITICAL
    stored = await services.ledger.signals_of_case(case.id)
    assert [(signal.code, signal.official) for signal in stored] == [(OFFICIAL_WARNING, True)]
    await services.ledger.delete_case(case.id)


def test_a_quote_that_is_not_in_the_notice_supports_nothing(clock: FakeClock) -> None:
    """S02.58 una cita que no aparece en el aviso no sostiene su señal."""
    text = "Rentabilidad garantizada del 40% en una semana"
    real = signal_of(clock, Analysis.PATTERNS, evidence=evidence_of(clock, "garantizada del 40%"))
    invented = signal_of(clock, Analysis.PATTERNS, evidence=evidence_of(clock, "sin riesgo alguno"))

    assert grounded((real, invented), text) == (real,)
    assert grounded((invented,), text) == ()


async def test_the_cli_analyses_and_prints_the_verdict(
    services: Services, clock: FakeClock, capsys: pytest.CaptureFixture[str]
) -> None:
    """S02.59 la CLI analiza un aviso y escribe el veredicto con su evidencia."""
    options = parser().parse_args(["analyze", "Invierte y dobla tu dinero", "--link", "https://x.test"])
    assert (options.text, options.link) == ("Invierte y dobla tu dinero", ["https://x.test"])

    investigation = Investigation(
        signals=(
            signal_of(
                clock, Analysis.PATTERNS, evidence=evidence_of(clock, quote="dobla tu dinero")
            ),
        ),
        entities=(),
        missing=(),
    )

    class Wired:
        def __init__(self) -> None:
            self.services = services

        def investigators(self, case_id: str) -> Analysts:
            return analysts(investigation)

    code = await run_analyze(Wired(), "Invierte y dobla tu dinero", [])  # type: ignore[arg-type]
    printed = capsys.readouterr().out
    assert code == 0
    assert "MEDIO" in printed and "dobla tu dinero" in printed
    assert "Qué hacer:" in printed


async def test_confirming_a_case_makes_the_recidivism_visible(services: Services) -> None:
    """S02.60 marcar un caso confirmado hace visible la reincidencia en el siguiente."""
    domain = f"reincidente-{uuid4().hex[:10]}.test"
    first = await opened_case(services, notice_of(f"Aviso sobre {domain}"))
    investigator, narrator = analysts(EMPTY)
    await analyze_case(services, investigator, narrator, case_id=first.id)

    marked = await review_case(services, case_id=first.id, review=ReviewState.CONFIRMED)
    assert marked is not None and marked.review_state is ReviewState.CONFIRMED

    second = await opened_case(services, notice_of(f"Otra vez {domain}"))
    analyzed = await analyze_case(services, investigator, narrator, case_id=second.id)
    assert isinstance(analyzed, Analyzed)
    assert analyzed.verdict.level is RiskLevel.CRITICAL
    codes = [signal.code for signal in await services.ledger.signals_of_case(second.id)]
    assert codes == [PRIOR_CONFIRMED_CASE]
    await services.ledger.delete_case(first.id)
    await services.ledger.delete_case(second.id)


def test_the_case_renders_with_its_evidence(clock: FakeClock) -> None:
    """S02.61 la salida de la CLI enseña el nivel, la evidencia y qué hacer."""
    from argos.usecases.queries import SignalView, VerdictSummary

    view = CaseView(
        id="c1",
        state=CaseState.VERDICT_ISSUED,
        review_state=ReviewState.UNREVIEWED,
        error=None,
        verdict=VerdictSummary(
            version=1,
            level=RiskLevel.CRITICAL,
            outcome=VerdictOutcome.ISSUED,
            summary="Coincide con una advertencia vigente.",
            actions=("No envíes dinero.",),
            missing=(),
        ),
        signals=(
            SignalView(
                analysis=Analysis.REGISTRIES,
                code=OFFICIAL_WARNING,
                strength=Strength.STRONG,
                official=True,
                recidivism=False,
                source="https://warnings.fca.example/demo",
                quote="La FCA mantiene una advertencia vigente.",
            ),
        ),
    )
    printed = render(view)
    assert "CRÍTICO" in printed
    assert "·oficial" in printed
    assert "https://warnings.fca.example/demo" in printed
    assert "No envíes dinero." in printed
