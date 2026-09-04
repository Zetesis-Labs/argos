# Argos

Segunda opinión ante un posible fraude financiero. Argos recibe un aviso —un
mensaje o un enlace— y produce un veredicto explicado: nivel de riesgo, indicios
con evidencia, entidades implicadas, reincidencias y acciones recomendadas.

No afirma que algo sea una estafa. Los agentes reúnen e interpretan evidencias;
un núcleo determinista valida señales, calcula el nivel y gobierna los estados.

## Estado del proyecto

Argos es una prueba de concepto deliberadamente pequeña: un proceso, tres
capacidades y el catálogo curado que viaja con el checkout.

- **S01 implementada y verificada**: SurrealDB 3 con MCP, separación
  `agno/sessions` y `argos/ops`, LiteLLM, devcontainer y anclaje de specs a tests.
- **S02 implementada**: caso con su aviso, extracción de identificadores por
  código, señales oficiales y de reincidencia derivadas del catálogo y de la
  memoria, cuatro agentes de apoyo, núcleo de puntuación y gateway con sus tres
  capacidades.
- **S03 implementada**: corpus OKF en Markdown/Git, bundle validado y
  versionado, explorador gráfico opcional y proyección local atómica en
  SurrealDB.
- Las verticales siguientes —dominio, puntuación con datos reales, fuentes
  oficiales y memoria— siguen el orden de `specs/README.md`.

La vertical asíncrona anterior —AgentOS, A2A, NATS JetStream, RustFS, worker de
PDF, dispatcher, resumer, analizador y janitor— se implementó, se verificó y se
retiró el 2026-09-04 por desproporcionada. Su diseño está en
`specs/parked/S02-pipeline-asincrono.md` y su código en el tag `s02-async`.

## Arquitectura

```text
POST /v1/notices → gateway → núcleo (identificadores, señales, nivel)
                                 │              │
                            SurrealDB      triage · patterns
                            argos/ops      writer · conversation
                                 │
                    proyección del catálogo OKF
```

- **El gateway** es el único proceso de larga vida. Analiza el aviso dentro de
  la llamada: no hay cola, no hay trabajo durable y no hay nada que reanudar.
- **`argos/core`** no hace I/O. Ahí viven la extracción de identificadores, el
  filtro de evidencia, la puntuación y la composición del veredicto.
- **SurrealDB `argos/ops`** es la fuente de verdad de casos, entidades, señales
  y veredictos. Un agente solo la lee por herramientas acotadas por capacidad,
  tenant y caso.
- **El conocimiento curado** se escribe como fichas Markdown en Git. El bundle
  OKF versionado alimenta el explorador y una proyección reconstruible en
  SurrealDB; Argos no necesita red para analizar.
- **SurrealDB `agno/sessions`** pertenece al runtime de Agno y guarda
  referencias, no el aviso.
- **LiteLLM** es la única pasarela a modelos. El único proveedor externo
  soportado es OpenAI, y el modelo por defecto es `mock`.

## Qué decide el código y qué decide el modelo

| Decisión | Quién |
|---|---|
| Identificadores del aviso | código (regex, dígitos de control, dominio registrable) |
| Advertencia oficial vigente | código, consultando el catálogo proyectado |
| Reincidencia confirmada | código, consultando la memoria compartida |
| Tipología, manipulación, indicios del texto | agentes, con cita literal obligatoria |
| Nivel de riesgo | `core.score`, nunca un prompt |
| Explicación del nivel | `verdict_writer`, sin poder cambiarlo |

Un informe de un modelo que se declara oficial o reincidente se descarta al
parsearlo, y una señal cuya cita no aparece en el aviso no puntúa.

## Agentes

| Componente | Cometido |
|---|---|
| `triage_agent` | Interpretar el aviso, proponer tipologías y consultar contexto |
| `patterns_agent` | Detectar patrones de manipulación con citas literales |
| `verdict_writer` | Explicar un nivel calculado por código |
| `conversation_agent` | Responder sobre un caso sin mutar su veredicto |

## Capacidades del gateway

| Capacidad | Ruta |
|---|---|
| `analyze_notice` | `POST /v1/notices` |
| `get_case` | `GET /v1/cases/{case_id}` |
| `ask_case` | `POST /v1/cases/{case_id}/questions` |

El tenant sale siempre de la credencial, nunca del cuerpo.

## Arrancar el entorno

Con Dev Containers basta con «Reopen in Container»: Compose levanta SurrealDB,
LiteLLM y el contenedor de trabajo, y una tarea idempotente aplica el esquema y
proyecta el bundle de conocimiento incluido en el checkout. Una SurrealDB de
test aislada permite ejecutar la suite mientras el producto sigue activo.

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
docker exec argos-app-1 uv run pytest
docker exec argos-app-1 uv run spec-check
docker exec argos-app-1 uv run ruff check .
docker exec argos-app-1 uv run mypy
docker exec argos-app-1 uv run pyright
```

No se ejecutan tests, lint, tipos ni builds desde el host.

`bootstrap-local` es la única preparación del entorno; `bootstrap-db` y
`project-knowledge` siguen disponibles para diagnosticar cada pieza por separado.

`project-knowledge` valida `knowledge/dist/okf-graph.json` y activa en una sola
transacción sus nodos, relaciones y tres advertencias sintéticas. Sus URLs usan
dominios reservados `.example`; no son datos reales ni sustituyen la ingesta de
fuentes oficiales. La advertencia activa de dominio puede comprobarse enviando
un aviso que cite `example-broker.test`: el veredicto sale `critical` por código,
sin que intervenga el modelo.

El explorador no bloquea Argos. Para reconstruirlo y servirlo opcionalmente en
`http://localhost:8400`:

```bash
docker compose -f .devcontainer/docker-compose.yml --profile docs up -d knowledge
```

Tras curar fichas, el bundle se regenera dentro de ese contenedor con
`docker compose -f .devcontainer/docker-compose.yml --profile docs run --rm knowledge bash okf/update-bundle.sh`
y se revisa junto al corpus en Git.

| Servicio | URL en el host | Perfil |
|---|---|---|
| Gateway | `http://localhost:7777` | `services` |
| LiteLLM | `http://localhost:4100` | por defecto |
| SurrealDB | `http://localhost:8100` (MCP en `/mcp`) | por defecto |
| Surrealist | `http://localhost:8200` | `tools` |
| Conocimiento OKF | `http://localhost:8400` | `docs` |

## Documentación

Lee en este orden:

1. [`specs/constitution.md`](specs/constitution.md): invariantes del proyecto.
2. [`specs/argos/veredicto/functional-specs.md`](specs/argos/veredicto/functional-specs.md): comportamiento del producto.
3. [`specs/argos/conocimiento/functional-specs.md`](specs/argos/conocimiento/functional-specs.md): curación, exploración y proyección.
4. [`specs/S01-plataforma.md`](specs/S01-plataforma.md): base ya verificada.
5. [`specs/S02-nucleo-y-agentes.md`](specs/S02-nucleo-y-agentes.md): caso, señales, agentes y gateway.
6. [`specs/S03-conocimiento-okf.md`](specs/S03-conocimiento-okf.md): fundación ejecutable de conocimiento.
7. [`specs/README.md`](specs/README.md): anclaje, estado e índice de fases.

## Reglas de calidad

- Python 3.12+ y Agno 3.x.
- `ruff`, `mypy` y `pyright` en estricto.
- `typing.Any` y las supresiones de tipos están prohibidos, también en tests.
- El LLM no puntúa, no decide permisos y no controla transiciones.
- Ningún proveedor de modelos se llama fuera de LiteLLM.
- Ningún aviso completo entra en sesiones, logs ni errores públicos.
- Datos de prueba exclusivamente sintéticos.
