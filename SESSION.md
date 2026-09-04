# Sesión: de SaaS multi-tenant a herramienta local — actualizado 2026-09-04

## Qué es Argos

Un devcontainer que te bajas y ejecutas en tu máquina. Un solo usuario: quien lo
arranca. `argos analyze "…"` devuelve un veredicto explicado. Nada de lo que
analizas sale de ahí.

## Qué se ha hecho en esta sesión

**Primer recorte: la vertical asíncrona.** Fuera AgentOS, A2A, NATS JetStream,
RustFS, Langfuse, el libro de trabajos con outbox e intentos, el worker de
PDF/OCR y los procesos dispatcher, resumer, analizador y janitor. Diseño en
`specs/parked/S02-pipeline-asincrono.md`, código en el tag `s02-async`.

**Segundo recorte: la multitenencia.** Rubén señaló que va contra el producto.
Fuera la tabla `tenant` y el `tenant_id` de cuatro tablas, `core/identity.py`
entero, los tokens bearer, los roles, el curador, `reviewed_by`, R16 tal como
estaba y cinco casos de spec que probaban aislamiento entre clientes que no
existen. Atravesaba 19 de 53 ficheros.

Con ello cayeron tres cosas más, decididas explícitamente:

- **Errores públicos**: fuera `public_code` y su catálogo. El destinatario de un
  fallo es quien ejecuta Argos; se le muestra entero.
- **Retención**: fuera `expires_at`. Un análisis local se guarda hasta que su
  dueño lo borra.
- **Deduplicación por ventana**: fuera. Repetir un aviso abre un caso nuevo; la
  ventana devolvía casos muertos durante 24 h.

**Añadido: la CLI.** `argos analyze/show/ask/review` es la superficie principal,
con la API HTTP local debajo compartiendo cableado (`argos/wiring.py`).

**Cerrados tres huecos operativos** que el recorte destapó:

- `analyze_case` relanzaba y devolvía un 500 de uvicorn; ahora devuelve `Failed`
  con el error real y el caso queda `failed`, nunca colgado en `analyzing`.
- R15 era decorativo; ahora el presupuesto se aplica con `asyncio.timeout`.
- `/health` mentía; ahora responde 503 si el libro no contesta.
- Nada escribía `review_state`, así que la reincidencia era inalcanzable.
  `argos review <caso> confirmed` la desbloquea.

## Estado de la puerta

Dentro de `argos-app-1`: `pytest` 64 passed, `spec-check`, `ruff`, `mypy` y
`pyright` limpios. Esquema en versión 7.

Probado de punta a punta con **gpt-5.6-terra real** (la clave está en
`.devcontainer/.env`, así que la prueba costó dinero): un aviso que cita
`example-broker.test` sale `CRÍTICO` con la advertencia de la FCA como indicio
oficial escrito por el código, y las señales inventadas por el modelo se
descartan si su cita no está en el aviso.

## Lo que sigue abierto

- **Ningún veredicto medido contra datos reales.** Es el trabajo número uno.
- **El catálogo tiene tres advertencias sintéticas.** Un aviso que no las cite
  sale `undetermined` o `low`. Lo arregla la vertical de fuentes (S08), que es
  el primer punto del roadmap.
- **Nadie loguea**: `structlog` está en las dependencias sin un solo uso.
  Operar el API es a ciegas; la CLI al menos imprime.
- **El modelo `mock` no cumple el contrato del informe**, así que el camino
  feliz solo se ve con una clave real. Decidido dejarlo así de momento.
- **`docs/revision-2026-09-04.md` sigue sin commitear.** Repo público. Decisión
  de Rubén.
