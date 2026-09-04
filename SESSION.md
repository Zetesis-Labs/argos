# Sesión: recorte a prueba de concepto — actualizado 2026-09-04

## Objetivo

Reducir Argos a lo que una prueba de concepto necesita para producir un
veredicto útil, y cerrar el agujero que impedía producirlo: el aviso nunca
llegaba al analizador y las marcas decisivas las declaraba el modelo.

## Qué se ha hecho

**Retirada la vertical asíncrona.** Fuera AgentOS, A2A, NATS JetStream, RustFS,
Langfuse, el libro de trabajos con outbox e intentos, el worker de PDF/OCR y los
procesos dispatcher, resumer, analizador y janitor. Su diseño queda en
`specs/parked/S02-pipeline-asincrono.md` y su código en el tag `s02-async`.

De 20 contenedores a 4 por defecto (`app`, `surrealdb`, `surrealdb-test`,
`litellm`; `surrealist`, `knowledge` y `gateway` bajo perfil). De 6 procesos de
Argos a 1. De 8 agentes más equipo a 4 sin equipo. LiteLLM pasa a la imagen sin
base de datos, así que Postgres desaparece con Langfuse.

**Cerrado el agujero de W1.** El caso guarda el texto del aviso y sus enlaces, y
`build_brief` los pasa al prompt. Antes el caso solo guardaba un hash y el brief
solo llevaba referencias de extracción: con documentos fuera, no había nada que
analizar.

**Señales decisivas por código.** `argos/core/identifiers.py` extrae dominio,
correo, IBAN, teléfono, wallet y arroba con expresiones regulares, dígitos de
control ISO 13616 y dominio registrable. `argos/usecases/signals.py` consulta el
catálogo proyectado y la memoria compartida y escribe las señales `official` y
`recidivism`. El parser del informe del modelo descarta esos campos, y una señal
cuya cita no aparece literalmente en el aviso no puntúa.

**Specs alineadas.** Constitución reescrita en commit propio: §9 y §10 pasan de
«NATS es el bus» y «RustFS es el almacén» a invariantes de cuándo y cómo
volverán. A12 de la funcional cambia: el análisis de un aviso breve ocurre dentro
de la llamada. S02 se reescribe como `specs/S02-nucleo-y-agentes.md` con 22
casos; los números que sobreviven conservan su significado.

## Estado de la puerta

Dentro de `argos-app-1`: `pytest` 59 passed, `spec-check`, `ruff`, `mypy` y
`pyright` limpios. Esquema en versión 6.

## Lo que sigue abierto

- **Ningún veredicto medido contra datos reales.** Es el motivo del recorte y
  sigue siendo el trabajo pendiente número uno: un conjunto de avisos reales
  etiquetados con el que calibrar la escalera de R4.
- **`docs/revision-2026-09-04.md` sigue sin commitear.** El repo es público y el
  informe documenta que las afirmaciones de cierre de S02 no eran exactas.
  Decisión de Rubén.
- Verticales siguientes según `specs/README.md`: dominio, puntuación con datos
  reales, fuentes oficiales y memoria.
- El explorador OKF y el corpus no han cambiado; S03 sigue en verde.
