# Argos

Segunda opinión ante un posible fraude financiero. Argos recibe mensajes,
enlaces, capturas o documentos PDF y produce un veredicto explicado: nivel de
riesgo, indicios con evidencia, entidades implicadas, reincidencias y acciones
recomendadas.

No afirma que algo sea una estafa. Los agentes reúnen e interpretan evidencias;
un núcleo determinista valida señales, calcula el nivel y gobierna los estados.

## Estado del proyecto

- **S01 implementada y verificada**: SurrealDB 3 con MCP, separación
  `agno/sessions` y `argos/ops`, LiteLLM, Langfuse, devcontainer y anclaje de
  specs a tests.
- **S02 implementada**: libro de trabajos y outbox, NATS JetStream, RustFS,
  worker de documentos, clúster de agentes, workflow de veredicto, gateway con
  sus capacidades, janitor de retención, métricas, una identidad por workload y
  advertencias sintéticas de demostración. Sus 55 casos tienen test.
- Las verticales del análisis real —identificadores, dominio, puntuación,
  veredicto, fuentes y memoria— siguen el orden de `specs/README.md`.

AgentOS sirve el gateway actual en el puerto `7777`. El devcontainer arranca los
seis procesos de larga vida por separado después de preparar la plataforma.

## Arquitectura objetivo

```text
API / A2A → AgentOS gateway → workflow + equipo de agentes
                                   │ MCP
                              SurrealDB
                                   │ outbox
                              NATS JetStream → workers
                                                    │
                                               RustFS
```

- **Agno AgentOS** aloja el gateway, los agentes especialistas, el equipo de
  investigación y el workflow de veredicto.
- **A2A** comunica AgentOS separados y expone capacidades completas del gateway.
  Dentro de una instancia, Agno coordina mediante Team y Workflow.
- **SurrealDB `argos/ops`** es la fuente de verdad para casos, grafo, trabajos,
  extracciones y outbox. Los agentes acceden mediante herramientas MCP acotadas.
- **El conocimiento curado** se versiona en Git y se carga en SurrealDB para su
  consulta local. Argos no necesita un servicio remoto para analizar.
- **SurrealDB `agno/sessions`** pertenece al runtime de Agno y no sustituye la
  memoria operacional.
- **NATS JetStream** entrega comandos y eventos por referencia. Los mensajes
  llevan `job_id` e `attempt`; no transportan PDFs, texto completo ni resultados.
- **RustFS** guarda artefactos privados S3-compatible. El código depende del
  puerto neutral `S3ObjectStore`, que escribe en flujo calculando el hash, lee
  de forma acotada y firma URLs breves.
- **Workers stateless** ejecutan tareas pesadas como extracción y OCR de PDF. No
  son agentes, no se exponen por A2A y no toman decisiones de riesgo. El de
  documentos lee con pypdfium2 y solo pasa por Tesseract las páginas sin texto
  utilizable.
- **LiteLLM** es la única pasarela a modelos. El único proveedor externo
  soportado es OpenAI; **Langfuse** recibe las trazas sin contenido sensible.

El documento, extracción y chunks pertenecen al tenant y al caso, nunca al
agente o worker. Una sesión conserva solo referencias, por lo que el caso puede
reanudar aunque desaparezca quien inició el trabajo.

## Agentes

| Componente | Cometido |
|---|---|
| `triage_agent` | Extraer identificadores, idioma y tipologías candidatas |
| `registries_agent` | Consultar advertencias oficiales |
| `domain_agent` | Analizar registro, certificado y reputación de dominios |
| `patterns_agent` | Detectar patrones de manipulación con citas |
| `memory_agent` | Encontrar entidades y casos previos |
| `document_agent` | Consultar trabajos, manifiestos y fragmentos autorizados; no crea ni reprocesa trabajos |
| `verdict_writer` | Explicar un nivel calculado por código |
| `conversation_agent` | Responder sobre un caso sin mutar su veredicto |
| `investigation_team` | Coordinar especialistas de análisis |
| `verdict_workflow` | Controlar estados, tiempo, degradación y cierre |

## Capacidades del gateway

| Capacidad | Ruta | Quién |
|---|---|---|
| `analyze_notice` | `POST /v1/notices` | servicio |
| `submit_document` | `POST /v1/documents` | servicio |
| `get_job` | `GET /v1/jobs/{job_id}` | servicio |
| `get_case` | `GET /v1/cases/{case_id}` | servicio |
| `ask_case` | `POST /v1/cases/{case_id}/questions` | servicio |
| `reprocess_document` | `POST /v1/documents/{document_id}/reprocess` | curador |

El tenant sale siempre de la credencial, nunca del cuerpo. La tarjeta de agente
está en `/.well-known/agent-card.json` y declara como habilidades esas
capacidades; las de texto se atienden además por JSON-RPC en
`POST /v1/a2a/messages`. Los especialistas y los workers no se publican: no
tienen tarjeta ni endpoint, y el plano de control de AgentOS es del curador.

`analyze_notice` espera el estado terminal hasta el presupuesto de R15 y, si no
llega, responde `202` con el caso en curso: el análisis es un trabajo durable y
sobrevive al proceso que atendió la llamada.

## Procesamiento de PDF

1. El gateway valida y guarda el original privado en RustFS.
2. Crea documento, trabajo y outbox en una operación durable de SurrealDB.
3. El dispatcher publica `argos.jobs.document.extract.v1` en NATS.
4. El worker relee el trabajo, extrae texto/OCR y confirma derivados y evento de
   outbox en una misma transacción.
5. El dispatcher publica el evento con referencias; el resumer crea el trabajo
   de análisis del caso y el workflow relee SurrealDB y reanuda.
6. El analizador reclama ese trabajo, pasa el caso a `analyzing` y ejecuta el
   equipo de investigación; el agente obtiene únicamente fragmentos autorizados
   y acotados por presupuesto.
7. El núcleo calcula el nivel, el redactor lo explica sin poder cambiarlo y el
   caso cierra con su veredicto versionado y su evento en el outbox.

La entrega es al menos una vez y el efecto es idempotente por documento,
versión de extractor y opciones; un documento se identifica dentro de su caso
por el hash del contenido. Un fallo terminal queda operable en SurrealDB para
inspección y reproceso. Todo análisis de caso, también el de un aviso breve,
es un trabajo durable que sobrevive al proceso que atendió la llamada.

## Arrancar el entorno

Con Dev Containers basta con «Reopen in Container»: Compose levanta la
infraestructura y los seis procesos, y una tarea idempotente aplica el esquema,
declara JetStream, crea el bucket y carga el conocimiento sintético local.
SurrealDB y NATS de test están aislados, por lo que la suite puede ejecutarse
mientras el producto sigue activo sin que sus workers consuman datos de prueba.

Sin la extensión, el mismo entorno completo se arranca desde el host con:

```bash
docker compose -f .devcontainer/docker-compose.yml --profile services up -d --build
```

No hace falta crear `.devcontainer/.env`; el modelo por defecto es `mock` y no
consume una API externa. Para cambiar puertos, credenciales locales o activar
OpenAI con `OPENAI_API_KEY` y `ANALYSIS_MODEL=gpt-5.6-terra`, se copia la
plantilla opcional:

```bash
cp .env.example .devcontainer/.env
```

Para comprobar el checkout:

```bash
docker exec argos-app-1 uv run rehearse-store
docker exec argos-app-1 uv run pytest
docker exec argos-app-1 uv run spec-check
docker exec argos-app-1 uv run ruff check .
docker exec argos-app-1 uv run mypy
docker exec argos-app-1 uv run pyright
```

No se ejecutan tests, lint, tipos ni builds desde el host.

`bootstrap-local` es la única preparación del entorno. `bootstrap-db`,
`bootstrap-bus`, `bootstrap-store` y `seed-demo-warnings` siguen disponibles
para diagnosticar cada pieza por separado.

`seed-demo-warnings` reconcilia de forma idempotente tres advertencias del
fixture `tests/fixtures/synthetic_warnings.json`. Sus URLs usan el dominio
reservado `.example`; no son datos reales ni sustituyen la ingesta de fuentes
de S07. La advertencia activa de dominio puede consultarse con
`example-broker.test`. El modelo `mock` no extrae identificadores ni produce
señales, por lo que una demostración completa del veredicto seguirá devolviendo
`undetermined` hasta implementar S03 o usar un investigador controlado.

| Servicio | URL en el host |
|---|---|
| AgentOS | `http://localhost:7777` (gateway: capacidades, tarjeta y plano de control) |
| LiteLLM | `http://localhost:4100` |
| Langfuse | `http://localhost:3200` |
| SurrealDB | `http://localhost:8100` (MCP en `/mcp`) |
| Surrealist | `http://localhost:8200` |
| NATS JetStream | `nats://localhost:4300` (monitor en `http://localhost:8300`) |
| RustFS | `http://localhost:9390` (consola en `http://localhost:9391`) |

El compose incluye Redis y un almacén MinIO exclusivamente como dependencias
internas de Langfuse. El código de Argos no los usa como cola ni como almacén
de artefactos: la cola es NATS JetStream y el almacén es RustFS.

## Documentación

Lee en este orden:

1. [`specs/constitution.md`](specs/constitution.md): invariantes del proyecto.
2. [`specs/argos/veredicto/functional-specs.md`](specs/argos/veredicto/functional-specs.md): comportamiento del producto.
3. [`specs/S01-plataforma.md`](specs/S01-plataforma.md): base ya verificada.
4. [`specs/S02-agentos-workers.md`](specs/S02-agentos-workers.md): arquitectura completa del clúster y workers.
5. [`specs/README.md`](specs/README.md): anclaje, estado e índice de fases.

## Reglas de calidad

- Python 3.12+ y Agno 3.x.
- `ruff`, `mypy` y `pyright` en estricto.
- `typing.Any` y las supresiones de tipos están prohibidos, también en tests.
- El LLM no puntúa, no decide permisos y no controla transiciones.
- Ningún proveedor de modelos se llama fuera de LiteLLM.
- Ningún documento completo entra en sesiones, NATS, logs o trazas.
- Datos de prueba exclusivamente sintéticos.
