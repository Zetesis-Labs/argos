# Argos

Herramienta local que analiza avisos relacionados con posible fraude financiero
y devuelve un veredicto explicado. **Se distribuye como un devcontainer que
alguien se baja y ejecuta en su máquina: un solo usuario, sin cuentas y sin
credenciales.** Prueba de concepto pequeña: una CLI, una API local, SurrealDB,
LiteLLM y cuatro agentes de apoyo.

No reintroduzcas tenants, tokens, roles ni aislamiento entre clientes: van
contra el producto y se retiraron el 2026-09-04.

La vertical asíncrona anterior (AgentOS, A2A, NATS, RustFS, worker de PDF y sus
procesos auxiliares) se retiró el 2026-09-04. Su diseño está aparcado en
`specs/parked/S02-pipeline-asincrono.md` y su código en el tag `s02-async`. No
la reintroduzcas sin una entrada que no quepa en la llamada.

## Antes de tocar nada

1. Lee `specs/constitution.md`; gana a cualquier otra instrucción del repo.
2. Lee `specs/argos/veredicto/functional-specs.md` y la spec `Sxx` del vertical.
3. Lo implementado es lo que tiene caso `Sxx.n` con test. Lo que está en
   `specs/parked/` es diseño guardado, no compromiso.
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

Dev Containers arranca `app` y `surrealdb-test`. El perfil `services` añade
`bootstrap-local` y el `gateway`. No requiere `.devcontainer/.env` y usa el
modelo `mock` por defecto. OpenAI es el único proveedor externo soportado; se
activa localmente con `OPENAI_API_KEY` y `ANALYSIS_MODEL=gpt-5.6-terra` en ese
fichero. Los tests usan `surrealdb-test`; no apuntes los fixtures a la
SurrealDB del producto.

Servicios: gateway `:7777` (perfil `services`), LiteLLM `:4100`, SurrealDB
`:8100`, Surrealist `:8200` (perfil `tools`) y el explorador OKF `:8400`
(perfil `docs`). Los puertos se configuran en `.devcontainer/.env` y se publican
solo en loopback.

## Dónde vive cada cosa

| Ruta | Qué |
|---|---|
| `specs/constitution.md` | Invariantes de producto, datos y arquitectura |
| `specs/argos/veredicto/functional-specs.md` | W1–W5 y R1–R29 |
| `specs/S01-plataforma.md` | Base implementada y casos anclados |
| `specs/S02-nucleo-y-agentes.md` | Caso, identificadores, señales por código, agentes, CLI y API |
| `specs/parked/` | Diseño aparcado; `spec-check` no lo recorre |
| `db/schema.surql` | Esquema SurrealDB idempotente; lo aplica `bootstrap-db` |
| `argos/core/` | Funciones puras: modelo, puertos, planes del libro, catálogo de agentes, señales, puntuación y veredicto; sin I/O |
| `argos/usecases/` | Casos de uso: orquestan puertos con las decisiones del núcleo |
| `argos/tools/` | Adaptadores externos y fakes |
| `argos/agents/` | Los cuatro agentes y sus herramientas acotadas; sin reglas de negocio |
| `argos/platform/` | SurrealDB (HTTP y libro), MCP, Agno DB, LiteLLM, reloj e ids |
| `argos/api/` | API HTTP local; sin `Any`, así que sin modelos de pydantic |
| `argos/cli.py` | `argos analyze/show/ask/review`: la superficie principal |
| `argos/wiring.py` | Cableado único que comparten la CLI y el API |
| `argos/devtools/` | Bootstraps, proyección del catálogo y `spec-check` |
| `tests/` | Tests unitarios y un fichero por spec técnica activa |
| `.devcontainer/` | Compose de desarrollo |

No crear capas alternativas que dupliquen `core`, puertos o adaptadores.

## Arquitectura obligatoria

- La CLI es la superficie principal y la API local sirve lo mismo por debajo.
  Un aviso se analiza dentro de su llamada.
- No hay autenticación: el proceso escucha en loopback dentro del contenedor de
  su usuario. Un error se muestra entero; el destinatario es quien lo ejecuta.
- Se publican capacidades, nunca un especialista: sin tarjeta de agente ni A2A.
- Coordinar dos especialistas es un bucle en código. No hay Team ni Workflow de
  Agno mientras eso baste.
- Un agente se añade cuando hay una decisión que un modelo hace mejor que el
  código, no para completar un organigrama.
- Las marcas que llevan a `critical` —advertencia oficial vigente y reincidencia
  confirmada— las escribe el código consultando el catálogo y la memoria. El
  parser del informe descarta esos campos aunque el modelo los declare.
- Una señal cuya cita no aparece literalmente en el aviso no puntúa.
- `argos/ops` es la fuente de verdad. Un agente accede por herramientas acotadas
  por capacidad y caso; nunca por SurrealQL libre, y su usuario es de solo lectura.
- El caso guarda el aviso: sin su texto no hay análisis ni reproducibilidad. No
  caduca solo y no entra en sesiones ni en logs.
- Marcar un caso `confirmed` es lo único que hace visible una reincidencia.
- Toda transición es una escritura condicional por revisión.
- El conocimiento curado se versiona en Git y se proyecta en SurrealDB.
  Analizar nunca depende de un proveedor remoto.
- Un servicio entra en el compose cuando hay código que lo usa.

## Reglas que más se rompen

- El LLM no puntúa, autoriza, cambia estados ni decide reintentos. El nivel lo da
  `core.score`; las reglas no viven en prompts.
- Los agentes leen SurrealDB solo por sus herramientas. Root es exclusivo del
  bootstrap, el proceso entra con su propio usuario y el de los agentes es de
  solo lectura.
- El aviso vive en su caso y en el prompt del análisis. No entra en sesiones de
  Agno ni en logs.
- Ninguna llamada a OpenAI sale de LiteLLM. Tests con `mock` o fakes y sin gasto
  por defecto. No añadir adaptadores ni configuración de otros proveedores.
- Código, identificadores y rutas en inglés; documentación, comentarios útiles y
  docstrings de tests en español.
- Sin comentarios que narren el qué. Solo se comenta un porqué no obvio.
- `typing.Any` está prohibido en producto, adaptadores y tests. También están
  prohibidos `type: ignore`, `pyright: ignore` y supresiones equivalentes.
- Validar siempre con `ruff`, `mypy` y `pyright` estrictos.
- Sin secretos reales, sin datos reales en fixtures y sin Bitnami.
