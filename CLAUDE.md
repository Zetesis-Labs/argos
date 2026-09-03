# Argos

Sistema de agentes para analizar avisos y documentos relacionados con posible
fraude financiero. La base S01 usa SurrealDB/MCP, LiteLLM y Langfuse. La
arquitectura aprobada S02 añade AgentOS, A2A, NATS JetStream, RustFS y workers
stateless para trabajos pesados.

## Antes de tocar nada

1. Lee `specs/constitution.md`; gana a cualquier otra instrucción del repo.
2. Lee `specs/argos/veredicto/functional-specs.md` y la spec `Sxx` del vertical.
3. Distingue estado actual de arquitectura objetivo: S01 está implementada; de
   S02 solo está implementado lo que tiene caso `S02.n` en su §16.
4. Un comportamiento nuevo empieza por spec, sigue por un test cuyo docstring
   comienza con `Sxx.n` y termina en código. `uv run spec-check` vigila el
   anclaje.

## Ejecutar siempre en el devcontenedor

El contenedor de la app se llama `argos-app-1`. Desde el host:

```bash
docker compose -f .devcontainer/docker-compose.yml --profile services up -d --build
docker exec argos-app-1 uv run --frozen bootstrap-local
docker exec argos-app-1 uv run pytest
docker exec argos-app-1 uv run spec-check
docker exec argos-app-1 uv run ruff check .
docker exec argos-app-1 uv run mypy
docker exec argos-app-1 uv run pyright
```

Nunca ejecutar tests, lint, tipos o build desde el host.

Dev Containers arranca `app` y los servicios `gateway`, `dispatcher`, `worker`,
`resumer`, `analyzer` y `janitor`. Todos esperan a `bootstrap-local`, que
sincroniza el lock y prepara SurrealDB, NATS, RustFS y el conocimiento sintético.
No requiere `.devcontainer/.env` y usa el modelo `mock` por defecto.
OpenAI es el único proveedor externo soportado; se activa localmente con
`OPENAI_API_KEY` y `ANALYSIS_MODEL=gpt-5.6-terra` en ese fichero.
Los tests usan `surrealdb-test` y `nats-test`; no detengas los procesos reales
para ejecutarlos ni apuntes los fixtures a los backends del producto.

Servicios: AgentOS `:7777`, LiteLLM `:4100`, Langfuse
`:3200`, SurrealDB `:8100`, Surrealist `:8200`, NATS `:4300` con monitor
`:8300` y RustFS `:9390` con consola `:9391`. Los puertos se configuran en
`.devcontainer/.env` y se publican solo en loopback.

El MinIO y Redis del compose son dependencias internas de Langfuse. No son
infraestructura de aplicación de Argos: los trabajos van por NATS y los
artefactos a RustFS.

## Dónde vive cada cosa

| Ruta | Qué |
|---|---|
| `specs/constitution.md` | Invariantes de producto, datos y arquitectura |
| `specs/argos/veredicto/functional-specs.md` | W1–W5 y R1–R29 |
| `specs/S01-plataforma.md` | Base implementada y casos anclados |
| `specs/S02-agentos-workers.md` | Arquitectura aprobada del clúster, NATS, RustFS y PDFs |
| `db/schema.surql` | Esquema SurrealDB idempotente; lo aplica `bootstrap-db` |
| `argos/core/` | Funciones puras: modelo, puertos, planes del libro, catálogo de agentes, señales, puntuación y veredicto; sin I/O |
| `argos/usecases/` | Casos de uso: orquestan puertos con las decisiones del núcleo |
| `argos/tools/` | Adaptadores externos y fakes |
| `argos/agents/` | Especialistas, `investigation_team`, redactor y sus herramientas acotadas; sin reglas de negocio |
| `argos/platform/` | SurrealDB (HTTP y libro de trabajos), MCP, Agno DB, LiteLLM, trazas, reloj e ids |
| `argos/api/` | Gateway HTTP y tarjeta A2A sobre AgentOS; sin `Any`, así que sin modelos de pydantic |
| `argos/services/` | Procesos de larga vida: `dispatcher`, `worker`, `resumer`, `analyzer` y `janitor` |
| `argos/devtools/` | Bootstraps, carga idempotente de advertencias sintéticas, ensayo de RustFS y `spec-check` |
| `tests/` | Tests unitarios y un fichero por spec técnica activa |
| `.devcontainer/` | Compose de desarrollo |

Las rutas futuras de S02 se concretan al escribir sus primeros tests. No crear
capas alternativas que dupliquen `core`, puertos o adaptadores.

## Arquitectura obligatoria de S02

- AgentOS expone capacidades del gateway. Los especialistas no se publican de
  forma individual.
- Dentro de una instancia se coordina con Team y Workflow de Agno. A2A se usa
  entre AgentOS o por clientes remotos; los workers no son agentes A2A.
- Agentes previstos: triaje, registros, dominio, patrones, memoria, documentos,
  redacción y conversación; equipo de investigación y workflow de veredicto.
- El documento y sus derivados pertenecen a `tenant → case`, nunca al agente o
  worker. Las sesiones guardan referencias, no la única copia.
- `argos/ops` es la fuente de verdad. Los agentes acceden por herramientas MCP
  acotadas; los workers usan identidad propia y mínima.
- El conocimiento curado se versiona en Git y se carga en SurrealDB como
  proyección local. Analizar nunca depende de un proveedor remoto.
- RustFS guarda originales y derivados mediante el puerto neutral
  `S3ObjectStore`. Los agentes no reciben credenciales S3.
- NATS JetStream transporta `{job_id, attempt}`. El trabajo y outbox nacen juntos
  en SurrealDB. ACK solo después de persistir; entrega al menos una vez e
  idempotencia obligatoria.
- Redis queda fuera de la cola y memoria de Argos.
- Reiniciar AgentOS, dispatcher, resumer o worker no puede perder ni duplicar un
  resultado confirmado.

## Reglas que más se rompen

- El LLM no puntúa, autoriza, cambia estados ni decide reintentos. El nivel lo da
  `core.score`; las reglas no viven en prompts.
- Los agentes usan SurrealDB solo mediante MCP de negocio y dentro de su tenant.
  Root es exclusivo del bootstrap y cada workload entra con su propio usuario;
  el de los agentes es de solo lectura.
- Los workers son procesos deterministas y stateless, no agentes conversacionales.
- Nada del texto completo entra en sesiones de Agno, mensajes NATS, logs o
  trazas. Los avisos breves no se persisten íntegros.
- Ninguna llamada a OpenAI sale de LiteLLM. Tests con `mock` o fakes y sin gasto
  por defecto. No añadir adaptadores ni configuración de otros proveedores.
- Código, identificadores y rutas en inglés; documentación, comentarios útiles y
  docstrings de tests en español.
- Sin comentarios que narren el qué. Solo se comenta un porqué no obvio.
- `typing.Any` está prohibido en producto, adaptadores y tests. También están
  prohibidos `type: ignore`, `pyright: ignore` y supresiones equivalentes.
- Validar siempre con `ruff`, `mypy` y `pyright` estrictos.
- Sin secretos reales, sin datos reales en fixtures y sin Bitnami.
