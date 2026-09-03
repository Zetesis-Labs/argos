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
- **S02 especificada, aún no implementada**: AgentOS, clúster de especialistas,
  A2A, NATS JetStream, RustFS y worker de documentos.
- Las verticales del análisis real —identificadores, dominio, puntuación,
  veredicto, fuentes y memoria— siguen el orden de `specs/README.md`.

El puerto `7777` está reservado al futuro AgentOS; en S01 el contenedor de la
app permanece preparado para desarrollo y no sirve todavía la API final.

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
- **SurrealDB `agno/sessions`** pertenece al runtime de Agno y no sustituye la
  memoria operacional.
- **NATS JetStream** entrega comandos y eventos por referencia. Los mensajes
  llevan `job_id` e `attempt`; no transportan PDFs, texto completo ni resultados.
- **RustFS** guarda artefactos privados S3-compatible. El código dependerá de un
  puerto neutral `S3ObjectStore`.
- **Workers stateless** ejecutan tareas pesadas como extracción y OCR de PDF. No
  son agentes, no se exponen por A2A y no toman decisiones de riesgo.
- **LiteLLM** es la única pasarela a modelos y **Langfuse** recibe trazas sin
  contenido sensible.

El documento, extracción y chunks pertenecen al tenant y al caso, nunca al
agente o worker. Una sesión conserva solo referencias, por lo que el caso puede
reanudar aunque desaparezca quien inició el trabajo.

## Agentes previstos

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

## Procesamiento de PDF

1. El gateway valida y guarda el original privado en RustFS.
2. Crea documento, trabajo y outbox en una operación durable de SurrealDB.
3. El dispatcher publica `argos.jobs.document.extract.v1` en NATS.
4. El worker relee el trabajo, extrae texto/OCR y confirma derivados y evento de
   outbox en una misma transacción.
5. El dispatcher publica el evento con referencias; el resumer crea el trabajo
   de análisis del caso y el workflow relee SurrealDB y reanuda.
6. El agente obtiene únicamente chunks autorizados mediante MCP.

La entrega es al menos una vez y el efecto es idempotente por documento,
versión de extractor y opciones; un documento se identifica dentro de su caso
por el hash del contenido. Un fallo terminal queda operable en SurrealDB para
inspección y reproceso. Todo análisis de caso, también el de un aviso breve,
es un trabajo durable que sobrevive al proceso que atendió la llamada.

## Arrancar el entorno

Con Dev Containers: «Reopen in Container». Sin la extensión:

```bash
cp .env.example .env
docker compose -f .devcontainer/docker-compose.yml up -d --build
docker exec argos-app-1 uv sync
docker exec argos-app-1 uv run bootstrap-db
docker exec argos-app-1 uv run bootstrap-bus
docker exec argos-app-1 uv run pytest
docker exec argos-app-1 uv run spec-check
docker exec argos-app-1 uv run ruff check .
docker exec argos-app-1 uv run mypy
docker exec argos-app-1 uv run pyright
```

No se ejecutan tests, lint, tipos ni builds desde el host.

| Servicio disponible en S01 | URL en el host |
|---|---|
| AgentOS | `http://localhost:7777` (puerto reservado; servidor pendiente de S02) |
| LiteLLM | `http://localhost:4100` |
| Langfuse | `http://localhost:3200` |
| SurrealDB | `http://localhost:8100` (MCP en `/mcp`) |
| Surrealist | `http://localhost:8200` |
| NATS JetStream | `nats://localhost:4300` (monitor en `http://localhost:8300`) |

El compose incluye Redis y un almacén MinIO exclusivamente como dependencias
internas de Langfuse. El código de Argos no los usa como cola ni como almacén
de artefactos: la cola es NATS JetStream y el almacén será RustFS.

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
