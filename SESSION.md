# Sesión: S02.55 lista y entorno local completo — actualizado 2026-09-04

## Objetivo

Mantener S02 cerrada y demostrable y continuar hacia análisis útil. Argos debe
funcionar completo en local; el conocimiento curado se versionará en Git y se
cargará en SurrealDB como proyección local.

## Estado actual

**Hecho y verificado en `main`.** S02 completa (PR #1, rebase, 51 commits, rama
borrada). Los ocho pasos del §14 de `specs/S02-agentos-workers.md` tienen código
y caso hasta S02.53: esquema v4, puertos y fakes tipados, NATS + dispatcher, RustFS, worker
de PDF/OCR, clúster de agentes con workflow de veredicto, gateway sobre AgentOS,
janitor, métricas e identidad por workload.

- En el cierre de PR: `uv run pytest` → 125 passed; ruff, mypy y pyright limpios; `spec-check` con
  S02.1–S02.53 anclados. Todo dentro de `argos-app-1`.
- Cadena probada con los procesos reales: `submit_document` → dispatcher →
  worker → dispatcher → resumer → dispatcher → analyzer → veredicto versionado.
- Gateway probado por HTTP desde el host tras el arreglo de bind: subida
  multipart, `get_job`, `get_case`, JSON-RPC A2A, `/metrics`.
- `uv run rehearse-store` en verde contra RustFS.

**Hecho y verificado en esta rama.** S02.54 añade
`seed-demo-warnings`: carga tres advertencias sintéticas desde
`tests/fixtures/synthetic_warnings.json`, valida R11 y URLs `.example`, y
reconcilia por ID y revisión sin duplicar. Contra SurrealDB real: primera pasada
3 creadas; segunda pasada 3 sin cambios. Suite completa: 126 passed;
`spec-check`, ruff, mypy y pyright limpios.

**Hecho y verificado en esta rama.** S02.55 convierte el compose en
el arranque soportado del producto: `bootstrap-local` sincroniza el lock y
prepara esquema, JetStream, bucket y conocimiento; el perfil `services` arranca
`gateway`, `dispatcher`, `worker`, `resumer`, `analyzer` y `janitor` por
separado, y Dev Containers los incluye mediante `runServices`. No necesita
`.devcontainer/.env` y usa `mock` por defecto. Ensayo real: bootstrap `Exited
(0)`, seis
procesos estables y `/health` + Agent Card correctos. Suite completa: 127
passed; `spec-check`, ruff, mypy y pyright limpios.

**Dirección aclarada, sin implementar.** Se retiró la spec prematura del
dashboard y la reserva S11. La constitución y W3 fijan Git como fuente del
conocimiento curado y SurrealDB como su proyección local. Un dashboard local
del devcontainer con AG-UI queda como idea futura sin fase asignada.

**Proveedor local configurado.** OpenAI queda como único proveedor externo y
`gpt-5.6-terra` como modelo real de análisis. La clave se copió desde el `.env`
de Nixon a `.devcontainer/.env`, ignorado por Git y con permisos `0600`; la API confirmó
acceso al modelo. El secreto no se mostró ni entró en ningún fichero versionado.
S01.11 ancla esta configuración; LiteLLM es el único contenedor que recibe la
clave y los fixtures de test fuerzan `mock` aunque el entorno personal use un
modelo real. Un checkout sin
`.devcontainer/.env` continúa usando `mock`. Humo real por LiteLLM:
HTTP 200 y respuesta de `gpt-5.6-terra`. Suite: 128 passed; `spec-check`, ruff,
mypy y pyright limpios.

**Tests aislados del producto vivo.** El compose incluye `surrealdb-test` y
`nats-test` sin puertos públicos ni volúmenes del producto. El fixture global
apunta a ellos y fuerza `mock`; la suite completa pasa con los seis procesos
reales activos. Se retiraron `postCreateCommand` y `postStartCommand` porque el
servicio `bootstrap` ya es la única barrera de preparación del perfil y ejecutar
otra copia podía competir con los procesos recién arrancados.

**Lo que NO hace todavía**: el modelo por defecto es `mock` y el aviso breve no
lleva aún identificadores durables al analizador, así que una demo completa
sigue saliendo `partial` / `undetermined`. El núcleo R4 sí convierte una señal
oficial vigente en `critical`, y `registries_agent` ya puede consultar la fila.

**Sin desplegar**: no hay imagen de producción, ni Helm, ni CI. El producto se
ejecuta completo en el devcontenedor; `app` queda como terminal de trabajo y los
seis procesos tienen contenedor propio.

## Decisiones y porqués

- **Sin pydantic en `argos/api`**: cualquier subclase de `BaseModel` dispara
  `explicit-any` bajo `disallow_any_explicit`, prohibido por la constitución.
  Los cuerpos se leen campo a campo en `argos/api/payloads.py`.
- **No se usa la interfaz A2A de Agno**: publica agentes y equipos, y eso
  expondría a los especialistas. Argos sirve su propia tarjeta y un
  `message/send` resuelto por código determinista.
- **Cero agentes registrados en AgentOS**: se hizo para cumplir «no descubre
  especialistas». Fue pasarse: la constitución prohíbe publicar *especialistas*,
  no tener cara conversacional. Consecuencia: `/agents` devuelve `[]` y la UI de
  Agno muestra un AgentOS vacío. `conversation_agent` sí puede registrarse.
- **El chat solo lee**: crear o reprocesar trabajos es caso de uso del gateway,
  nunca herramienta de un agente (S02 §5). Subir un PDF lo hace la UI llamando
  al gateway.
- **El janitor borra el objeto antes de marcar la fila**: borrar es idempotente
  y marcar no; al revés, un fallo del almacén dejaba el objeto huérfano para
  siempre. La spec §12 pedía lo contrario y se corrigió.
- **Los derivados de extracción nacen como artefacto `uploading`**: si el cierre
  no llega a confirmarse (arrendamiento perdido), el janitor los recoge por TTL.
- **Una identidad de base de datos por workload** y `agent` en solo lectura:
  da rotación y auditoría independientes, no mínimo privilegio por tabla.
- **El sembrado demo sigue en S02, no abre S07**: solo prepara datos sintéticos
  para una herramienta ya existente; no descarga, cachea ni versiona fuentes.
- **URLs demo exclusivamente bajo `.example`**: hace verificable que ningún
  aviso real entra en el repositorio.
- **Argos es local-first**: analizar un caso no depende de una base ni servicio
  remoto de conocimiento.
- **Git es la fuente del conocimiento curado**: SurrealDB se reconstruye desde
  ese catálogo para consultar con rapidez y no diverge como fuente editorial.

## Hipótesis descartadas

- **`TestClient` de Starlette para probar la API**: abre su propio bucle de
  eventos y el websocket de SurrealDB no lo cruza (`RuntimeError: got Future
  attached to a different loop`). Se usa `httpx.ASGITransport` en el mismo bucle.
- **Ruta `/health` propia en el gateway**: AgentOS la pisa siempre. Se dejó que
  la sirva él.
- **Bind del gateway a `127.0.0.1`**: correcto para el compose, roto para el
  proceso — Docker no puede reenviar. Ver gotchas.
- **`password="otra-cosa"` en el test de identidades**: el escáner de secretos lo
  marca. Se prueba con la contraseña del workload vecino, que además es más fiel
  al caso.

## Pendientes

1. **S03 · identificadores**: llevar los identificadores normalizados del aviso
   hasta el análisis para que el catálogo local produzca el primer veredicto útil
   con una entrada realista y sintética.
2. **Dashboard local futuro, sin fase asignada**. Interfaz del devcontainer para
   operación, curación y conversación AG-UI; concretar solo cuando el análisis
   real sea útil.
3. **S07 · fuentes oficiales (W3)**: la ingesta real que llena `warning`.

El compose fue reconstruido después del cambio y el perfil `services` permanece
levantado en el proyecto local `argos`.

## Gotchas descubiertos

- **Un usuario `VIEWER` de SurrealDB no rechaza una escritura de datos**: la
  ejecuta sin efecto y responde `OK` con resultado vacío. Solo el DDL da error.
  Para comprobar que no escribe hay que mirar la fila, no el código.
- **Un proceso dentro del contenedor debe escuchar en `0.0.0.0`** o el reenvío
  de puertos de Docker no llega. La regla de loopback es del compose, que ya
  publica en `127.0.0.1` del host. Probar por `docker exec` entra por dentro y
  no detecta el fallo.
- **Los tests que consultan el libro globalmente son frágiles** contra la base
  de desarrollo compartida: un documento caducado de un humo anterior rompía el
  caso de retención. Acotar las aserciones al tenant propio.
- **GitGuardian escanea todos los commits del PR**, no solo el tip: arreglar el
  literal en la punta no cierra el incidente.
- **Sembrar `warning` no basta para una demo end-to-end con `mock`**: S03 de
  identificadores debe transportar al análisis lo necesario del aviso breve, o
  la prueba debe inyectar un investigador controlado.
- **Los tests de JetStream no pueden compartir consumidores con los procesos de
  desarrollo**: por eso `tests/conftest.py` fija los backends `*-test`. No volver
  a apuntarlos a los servicios del producto.

## Referencias

- PR mergeado: https://github.com/Zetesis-Labs/argos/pull/1
- `main` en `5adc47e`; arreglos posteriores al merge: `af4e56b` (bind),
  `5adc47e` (aislamiento del caso de retención).
- Specs: `specs/constitution.md`, `specs/argos/veredicto/functional-specs.md`,
  `specs/S02-agentos-workers.md` (§14 pasos, §16 casos).
- Datos demo: `argos/devtools/seed_warnings.py`,
  `tests/fixtures/synthetic_warnings.json`, comando `uv run seed-demo-warnings`.
- Arranque local: `argos/devtools/bootstrap_local.py`, perfil `services` en
  `.devcontainer/docker-compose.yml` y `runServices` en
  `.devcontainer/devcontainer.json`.
- Incidente GitGuardian resuelto por Rubén el 2026-09-03 (falso positivo).
