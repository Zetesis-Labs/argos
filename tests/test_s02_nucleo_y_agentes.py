"""Casos S02: caso, memoria compartida, señales por código, agentes y gateway."""

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
from argos.core.identity import Identity, Role
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
    Tenant,
    Update,
    VerdictOutcome,
    entity_id,
)
from argos.core.notices import Notice
from argos.core.observability import INTERNAL_ERROR, public_code
from argos.core.policy import AnalysisPolicy, Policy
from argos.core.ports import ConversationBrief, Investigation, Ledger
from argos.core.reports import NO_VERDICT_YET, investigation_prompt, parse_investigation
from argos.core.verdicts import plan_analysis_failure
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
from argos.usecases.analysis import Analyzed, Skipped, analyze_case, build_brief, grounded
from argos.usecases.deps import Services
from argos.usecases.gateway import Analysts, analyze_notice
from argos.usecases.notices import NoticeOpened, NoticeRefused, open_notice_case
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

LEDGER_TABLES = {"tenant", "case"}
MEMORY_TABLES = {"entity", "entity_link", "case_entity", "warning", "signal", "verdict"}
SHARED_TABLES = {"entity", "entity_link", "warning"}
RETIRED_TABLES = {"artifact", "document", "job", "attempt", "outbox_entry", "extraction", "chunk"}
TEST_POLICY = Policy(analysis=AnalysisPolicy(budget=timedelta(seconds=1)))
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
async def tenant(ledger: Ledger) -> AsyncIterator[Tenant]:
    record = Tenant(id=f"t-{uuid4().hex[:12]}", name="tenant de prueba", active=True, revision=0)
    await ledger.commit([Insert(record)])
    try:
        yield record
    finally:
        await ledger.delete_tenant_data(record.id)


@pytest.fixture
async def stranger(ledger: Ledger) -> AsyncIterator[Tenant]:
    record = Tenant(id=f"t-{uuid4().hex[:12]}", name="tenant ajeno", active=True, revision=0)
    await ledger.commit([Insert(record)])
    try:
        yield record
    finally:
        await ledger.delete_tenant_data(record.id)


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


async def opened_case(services: Services, tenant: Tenant, notice: Notice) -> Case:
    opened = await open_notice_case(
        services, tenant_id=tenant.id, notice=notice, correlation_id="corr-test"
    )
    assert isinstance(opened, NoticeOpened)
    return opened.case


def reviewed(case: Case, review: ReviewState, now: datetime) -> Update:
    return Update(replace(case, review_state=review, reviewed_at=now, revision=case.revision + 1))


def caller_of(agent: AgentName, tenant: Tenant, case_id: str) -> ToolCaller:
    return ToolCaller(agent=agent, tenant_id=tenant.id, case_id=case_id)


def analysts(investigation: Investigation, summary: str = "Resumen con indicios.") -> Analysts:
    return (ScriptedInvestigator(investigation), ScriptedNarrator(summary))


EMPTY = Investigation(signals=(), entities=(), missing=())


async def test_notice_opens_case_with_its_identifiers(
    services: Services, tenant: Tenant
) -> None:
    """S02.11 el aviso deja caso e identificadores en una transacción, aplica R1 y deduplica por R9."""
    notice = notice_of(f"Escribe a soporte@{DEMO_DOMAIN} o llama al +34600111222")
    first = await open_notice_case(
        services, tenant_id=tenant.id, notice=notice, correlation_id="corr-1"
    )
    assert isinstance(first, NoticeOpened)
    assert first.case.state is CaseState.RECEIVED and not first.reused
    assert first.case.notice_text == notice.text

    links = [link.entity_id for link in await services.ledger.entities_of_case(first.case.id)]
    assert entity_id(EntityKind.DOMAIN, DEMO_DOMAIN) in links
    assert entity_id(EntityKind.EMAIL, f"soporte@{DEMO_DOMAIN}") in links
    assert entity_id(EntityKind.PHONE, "+34600111222") in links

    again = await open_notice_case(
        services, tenant_id=tenant.id, notice=notice, correlation_id="corr-2"
    )
    assert isinstance(again, NoticeOpened)
    assert again.reused and again.case.id == first.case.id

    too_long = await open_notice_case(
        services,
        tenant_id=tenant.id,
        notice=notice_of("x" * (TEST_POLICY.notices.max_text_chars + 1)),
        correlation_id="corr-3",
    )
    assert again.case.revision == first.case.revision
    assert too_long == NoticeRefused("notice.text_too_long")
    assert await open_notice_case(
        services, tenant_id="fantasma", notice=notice, correlation_id="corr-4"
    ) == NoticeRefused("tenant.unknown")


async def test_schema_is_idempotent(settings: Settings, ledger_schema: None) -> None:
    """S02.27 el esquema de casos, memoria compartida, señales y veredictos es idempotente."""
    await apply_schema(settings)
    await apply_schema(settings)
    http = SurrealHttp(settings.surreal_url)
    info = await http.sql(
        "INFO FOR DB;", auth=settings.root_auth, ns=settings.ops_namespace, db=settings.ops_database
    )
    database = info[-1].result
    assert isinstance(database, dict)
    tables = names_in(database.get("tables"))
    assert tables >= LEDGER_TABLES | MEMORY_TABLES
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

    for table in sorted(MEMORY_TABLES):
        assert ("tenant_id" in await fields_of(table)) is (table not in SHARED_TABLES)
    assert {"review_state", "notice_text", "notice_links"} <= await fields_of("case")

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


def test_agents_only_declare_read_tools(services: Services, tenant: Tenant) -> None:
    """S02.30 cada agente declara solo sus herramientas y ninguna escribe en el libro."""
    assert set(AgentName) == {
        AgentName.TRIAGE,
        AgentName.PATTERNS,
        AgentName.VERDICT_WRITER,
        AgentName.CONVERSATION,
    }
    for agent in AgentName:
        bound = tools_for(services, caller_of(agent, tenant, "case-1"))
        assert {tool.capability for tool in bound} == capabilities_of(agent)
    assert set(Capability) == {
        Capability.GET_CASE_CONTEXT,
        Capability.FIND_REGISTRY_MATCHES,
        Capability.FIND_ENTITY_HISTORY,
    }


async def test_tools_refuse_foreign_agent_tenant_and_case(
    services: Services, tenant: Tenant, stranger: Tenant
) -> None:
    """S02.31 una herramienta rechaza al agente sin capacidad, a otro tenant y a otro caso."""
    case = await opened_case(services, tenant, notice_of("Un aviso cualquiera"))

    writer = await get_case_context(services, caller_of(AgentName.VERDICT_WRITER, tenant, case.id))
    foreign = await get_case_context(
        services, ToolCaller(agent=AgentName.TRIAGE, tenant_id=stranger.id, case_id=case.id)
    )
    unknown = await get_case_context(services, caller_of(AgentName.TRIAGE, tenant, "case:ghost"))
    allowed = await get_case_context(services, caller_of(AgentName.TRIAGE, tenant, case.id))

    assert writer == ToolDenied(NOT_AUTHORIZED)
    assert foreign == ToolDenied(CASE_NOT_FOUND)
    assert unknown == ToolDenied(CASE_NOT_FOUND)
    assert isinstance(allowed, CaseContext) and allowed.case_id == case.id


async def test_entity_history_only_returns_aggregates(
    services: Services, tenant: Tenant, stranger: Tenant
) -> None:
    """S02.33 find_entity_history devuelve al otro tenant solo agregados."""
    domain = f"inversiones-{uuid4().hex[:10]}.test"
    identifier = entity_id(EntityKind.DOMAIN, domain)
    now = services.clock.now()
    confirmed = await opened_case(services, tenant, notice_of(f"Confirmado sobre {domain}"))
    plain = await opened_case(services, tenant, notice_of(f"Otro aviso sobre {domain}"))
    await services.ledger.commit([reviewed(confirmed, ReviewState.CONFIRMED, now)])

    other_case = await opened_case(services, stranger, notice_of("Aviso ajeno"))
    history = await find_entity_history(
        services,
        ToolCaller(agent=AgentName.CONVERSATION, tenant_id=stranger.id, case_id=other_case.id),
        kind=EntityKind.DOMAIN,
        value=f"  {domain.upper()} ",
    )
    assert isinstance(history, EntityHistory)
    assert (history.cases, history.confirmed) == (2, True)
    assert history.first_seen_at is not None and history.last_seen_at is not None
    assert plain.id != confirmed.id
    assert identifier == entity_id(EntityKind.DOMAIN, domain)



async def test_analysis_moves_the_case_and_issues_the_verdict(
    services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.35 el análisis pasa el caso a analyzing y lo cierra con su veredicto versionado."""
    text = "Rentabilidad garantizada del 40% en nexolabs.test"
    case = await opened_case(services, tenant, notice_of(text))
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


async def test_a_notice_without_analyzable_content_is_insufficient(
    services: Services, tenant: Tenant
) -> None:
    """S02.36 un aviso sin entrada analizable termina insufficient y nunca en un nivel."""
    case = await opened_case(services, tenant, Notice(text="", image=b"\x89PNG\r\n\x1a\n" + b"0"))
    investigator, narrator = analysts(EMPTY)
    analyzed = await analyze_case(services, investigator, narrator, case_id=case.id)

    assert isinstance(analyzed, Analyzed)
    assert analyzed.case.state is CaseState.INSUFFICIENT
    assert analyzed.verdict.outcome is VerdictOutcome.INSUFFICIENT
    assert analyzed.verdict.level is RiskLevel.UNDETERMINED
    assert analyzed.verdict.actions


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
    tenant = Tenant(id=f"t-{uuid4().hex[:12]}", name="tenant real", active=True, revision=0)
    await services.ledger.commit([Insert(tenant)])
    try:
        case = await opened_case(services, tenant, notice_of(f"aviso sintetico {marker}"))
        cluster = build_cluster(
            services, settings, tenant_id=tenant.id, case_id=case.id, db=build_agno_db(settings)
        )
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

        sessions = await agno_session_dump(settings)
        assert marker not in sessions
    finally:
        await services.ledger.delete_tenant_data(tenant.id)


CURATOR_TOKEN = "test-curator"
SERVICE_TOKEN = "test-service"
OTHER_TOKEN = "test-other"


class ScriptedAdvisor:
    def __init__(self, answer: str = "El nivel no cambia; revisa las acciones.") -> None:
        self._answer = answer
        self.briefs: list[ConversationBrief] = []

    async def answer(self, brief: ConversationBrief) -> str:
        self.briefs.append(brief)
        return self._answer


def identities_of(tenant: Tenant, stranger: Tenant) -> dict[str, Identity]:
    return {
        SERVICE_TOKEN: Identity(name="dev", role=Role.SERVICE, tenant_id=tenant.id),
        OTHER_TOKEN: Identity(name="ajeno", role=Role.SERVICE, tenant_id=stranger.id),
        CURATOR_TOKEN: Identity(name="curador", role=Role.CURATOR, tenant_id=None),
    }


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def advisor() -> ScriptedAdvisor:
    return ScriptedAdvisor()


@pytest.fixture
def investigation() -> Investigation:
    return EMPTY


@pytest.fixture
def gateway_app(
    services: Services,
    tenant: Tenant,
    stranger: Tenant,
    advisor: ScriptedAdvisor,
    investigation: Investigation,
) -> FastAPI:
    gateway = Gateway(
        services=services,
        investigators=lambda tenant_id, case_id: analysts(investigation),
        advisors=lambda tenant_id, case_id: advisor,
        identities=identities_of(tenant, stranger),
        version="test",
    )
    return build_app(gateway)


@pytest.fixture
async def client(anyio_backend: str, gateway_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app), base_url="http://argos.test"
    ) as running:
        yield running


async def test_gateway_derives_the_tenant_from_the_identity(
    client: httpx.AsyncClient, stranger: Tenant
) -> None:
    """S02.38 el gateway deriva el tenant de la identidad y nunca del cuerpo."""
    body = {"text": "Invierte con Nexolabs y dobla tu dinero", "tenant_id": stranger.id}
    assert (await client.post("/v1/notices", json=body)).status_code == 401
    unknown = await client.post("/v1/notices", json=body, headers=bearer("desconocido"))
    assert unknown.status_code == 401

    accepted = await client.post("/v1/notices", json=body, headers=bearer(SERVICE_TOKEN))
    assert accepted.status_code == 200
    case_id = str(accepted.json()["case_id"])
    mine = await client.get(f"/v1/cases/{case_id}", headers=bearer(SERVICE_TOKEN))
    theirs = await client.get(f"/v1/cases/{case_id}", headers=bearer(OTHER_TOKEN))
    assert mine.status_code == 200 and theirs.status_code == 404

    curator = await client.post("/v1/notices", json=body, headers=bearer(CURATOR_TOKEN))
    assert curator.status_code == 403
    assert curator.json() == {"error": "identity.not_a_tenant"}


async def test_analyze_notice_returns_the_verdict_in_the_same_call(
    services: Services, tenant: Tenant, clock: FakeClock
) -> None:
    """S02.40 analyze_notice devuelve el veredicto en la misma llamada, sin trabajo durable."""
    investigation = Investigation(
        signals=(
            signal_of(
                clock,
                Analysis.PATTERNS,
                evidence=evidence_of(clock, quote="dobla tu dinero"),
            ),
        ),
        entities=(),
        missing=(),
    )
    analysis = await analyze_notice(
        services,
        lambda tenant_id, case_id: analysts(investigation),
        tenant_id=tenant.id,
        notice=notice_of("Invierte hoy y dobla tu dinero en una semana"),
        correlation_id="corr-40",
    )
    assert not isinstance(analysis, NoticeRefused)
    assert analysis.state is CaseState.VERDICT_ISSUED
    assert analysis.verdict is not None and analysis.verdict.version == 1
    assert not analysis.reused


async def test_api_hides_other_tenants(client: httpx.AsyncClient) -> None:
    """S02.42 la API no deja ver el caso de otro tenant."""
    accepted = await client.post(
        "/v1/notices", json={"text": "Un aviso propio"}, headers=bearer(SERVICE_TOKEN)
    )
    case_id = str(accepted.json()["case_id"])
    case = await client.get(f"/v1/cases/{case_id}", headers=bearer(OTHER_TOKEN))
    question = await client.post(
        f"/v1/cases/{case_id}/questions",
        json={"question": "¿qué sabes?"},
        headers=bearer(OTHER_TOKEN),
    )
    assert [case.status_code, question.status_code] == [404, 404]
    assert case.json() == {"error": "case.not_found"}


@pytest.mark.parametrize("investigation", [EMPTY], ids=["sin-informe"])
async def test_ask_case_answers_without_touching_the_verdict(
    client: httpx.AsyncClient,
    services: Services,
    tenant: Tenant,
    advisor: ScriptedAdvisor,
    investigation: Investigation,
) -> None:
    """S02.43 ask_case responde con la evidencia persistida y no muta el veredicto."""
    accepted = await client.post(
        "/v1/notices",
        json={"text": f"Promesa de rentabilidad en {DEMO_DOMAIN}"},
        headers=bearer(SERVICE_TOKEN),
    )
    case_id = str(accepted.json()["case_id"])
    before = await services.ledger.current_verdict(case_id)
    assert before is not None

    answered = await client.post(
        f"/v1/cases/{case_id}/questions",
        json={"question": "¿Puedo recuperar el dinero?"},
        headers=bearer(SERVICE_TOKEN),
    )
    assert answered.status_code == 200
    assert answered.json()["answer"] == "El nivel no cambia; revisa las acciones."
    assert advisor.briefs[0].level is before.level

    after = await services.ledger.current_verdict(case_id)
    assert after is not None and after.version == before.version

    pending = await opened_case(services, tenant, notice_of("Aviso todavía sin analizar"))
    unanswered = await client.post(
        f"/v1/cases/{pending.id}/questions",
        json={"question": "¿ya está?"},
        headers=bearer(SERVICE_TOKEN),
    )
    assert unanswered.status_code == 200
    assert unanswered.json()["answer"] == NO_VERDICT_YET
    assert unanswered.json()["verdict"] is None


async def test_a_settled_case_is_not_analysed_twice(
    services: Services, tenant: Tenant
) -> None:
    """S02.45 un caso ya cerrado no se vuelve a analizar ni duplica su veredicto."""
    case = await opened_case(services, tenant, notice_of("Aviso con un enlace a banco.test"))
    investigator, narrator = analysts(EMPTY)
    first = await analyze_case(services, investigator, narrator, case_id=case.id)
    assert isinstance(first, Analyzed)

    again = await analyze_case(services, investigator, narrator, case_id=case.id)
    assert isinstance(again, Skipped)
    current = await services.ledger.current_verdict(case.id)
    assert current is not None and current.version == 1


async def test_each_workload_has_its_own_identity(settings: Settings, ledger_schema: None) -> None:
    """S02.47 el gateway tiene su identidad y la de los agentes es de solo lectura."""
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
        f"CREATE tenant:{intruder} SET name = 'x', active = true, revision = 0;",
        auth=agent_auth,
        ns=settings.ops_namespace,
        db=settings.ops_database,
        raise_on_error=False,
    )
    written = await http.sql(
        f"SELECT * FROM tenant:{intruder};",
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


async def test_public_errors_do_not_leak_internals(services: Services, tenant: Tenant) -> None:
    """S02.51 el error público no filtra claves, SQL ni texto del aviso."""
    case = await opened_case(services, tenant, notice_of("promesa de rentabilidad garantizada"))
    leaked = (
        "SELECT * FROM case WHERE notice_text = 'promesa de rentabilidad garantizada' "
        "-- password=hunter2"
    )
    await services.ledger.commit(
        plan_analysis_failure(case=case, code=leaked, now=services.clock.now())
    )

    stored = await services.ledger.case(case.id)
    assert stored is not None and stored.public_error == leaked
    view = await get_case(services, tenant_id=tenant.id, case_id=case.id)
    assert isinstance(view, CaseView)
    rendered = dumps(case_payload(view))
    assert "SELECT" not in rendered and "hunter2" not in rendered
    assert "rentabilidad" not in rendered
    assert INTERNAL_ERROR in rendered
    assert public_code("notice.empty") == "notice.empty"



async def test_demo_warnings_are_queryable(settings: Settings) -> None:
    """S02.54 el catálogo sintético demuestra la consulta de advertencias oficiales."""
    ledger = InMemoryLedger()
    bundle = parse_knowledge_bundle(read_bundle(settings.knowledge_graph_path))
    warnings = warnings_from_bundle(bundle)
    await ledger.commit([Insert(warning) for warning in warnings])
    services = Services(
        ledger=ledger, clock=FakeClock(), ids=SequentialIds(), policy=TEST_POLICY
    )
    caller = ToolCaller(agent=AgentName.TRIAGE, tenant_id="tenant-demo", case_id="case-demo")

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
    assert "postStartCommand" not in devcontainer
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


async def test_only_code_marks_official_warnings_and_recidivism(
    services: Services, tenant: Tenant, stranger: Tenant, clock: FakeClock
) -> None:
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
    case = await opened_case(services, tenant, notice_of(f"Opera con {advertised} sin riesgo"))
    investigator, narrator = analysts(EMPTY)
    analyzed = await analyze_case(services, investigator, narrator, case_id=case.id)

    assert isinstance(analyzed, Analyzed)
    assert analyzed.verdict.level is RiskLevel.CRITICAL
    stored = await services.ledger.signals_of_case(case.id)
    assert [(signal.code, signal.official) for signal in stored] == [(OFFICIAL_WARNING, True)]
    assert PRIOR_CONFIRMED_CASE not in {signal.code for signal in stored}


def test_a_quote_that_is_not_in_the_notice_supports_nothing(clock: FakeClock) -> None:
    """S02.58 una cita que no aparece en el aviso no sostiene su señal."""
    text = "Rentabilidad garantizada del 40% en una semana"
    real = signal_of(clock, Analysis.PATTERNS, evidence=evidence_of(clock, "garantizada del 40%"))
    invented = signal_of(clock, Analysis.PATTERNS, evidence=evidence_of(clock, "sin riesgo alguno"))

    assert grounded((real, invented), text) == (real,)
    assert grounded((invented,), text) == ()
