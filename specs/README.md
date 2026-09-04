# Especificaciones ancladas

Tres capas, en este orden de autoridad:

1. `constitution.md`: principios. Gana a todo lo demás.
2. `argos/{iniciativa}/functional-specs.md`: la especificación **funcional** de
   una iniciativa (qué hace el producto para quién). Sus flujos llevan
   identificador `W1`, `W2`… y sus reglas `R1`, `R2`…
3. `Sxx-*.md`: specs **técnicas** por vertical, en casos `Dado / Cuando /
   Entonces` con identificador estable `S01.3`. Cada caso cita los `W`/`R` de la
   funcional que cubre.

Los tests verticales citan el identificador del caso en la primera línea de su
docstring:

```python
def test_mcp_lists_ops_tables() -> None:
    """S01.2 el usuario de los agentes ve las tablas de argos/ops por MCP."""
```

`uv run spec-check` falla si:

- un caso `Sxx.n` de una spec técnica no tiene ningún test que lo cite;
- un test cita un caso `Sxx.n` que no existe en ninguna spec.

Los flujos y reglas de la funcional (`W`, `R`) no exigen test propio: se
cubren a través de los casos `Sxx.n` que los citan. Un `W`/`R` sin ningún caso
técnico que lo cite es trabajo pendiente, no un error.

Las specs son el pliego: se escriben antes que el código y se cambian antes que
el código.

## Índice

| Spec | Vertical | Fase | Cubre |
|---|---|---|---|
| [funcional](argos/veredicto/functional-specs.md) | Veredicto de avisos y documentos (iniciativa v1) | todas | W1–W5, R1–R29 |
| [conocimiento](argos/conocimiento/functional-specs.md) | Catálogo curado, exploración y proyección local | todas | W1–W4, R1–R14 |
| [S01](S01-plataforma.md) | Base verificada: SurrealDB/MCP y LiteLLM | 0 | constitución §2, §7, §11–§12 |
| [S02](S02-nucleo-y-agentes.md) | Caso, identificadores, señales por código, agentes y gateway | 1 | W1, W2; R1–R9, R15, R16, R28, R29; constitución §3–§8, §11–§12 |
| [S03](S03-conocimiento-okf.md) | Fundación OKF en Git, explorador y proyección SurrealDB | 2 | conocimiento W1–W3, R1–R12 y R14 |

S01, S02 y S03 están implementadas y verificadas.

## La vertical asíncrona, aparcada

`specs/parked/S02-pipeline-asincrono.md` es la S02 original: AgentOS, A2A, libro
de trabajos, outbox, NATS JetStream, RustFS, worker de PDF y sus cuatro procesos
auxiliares. Se implementó entera, se verificó con 55 casos y se retiró el
2026-09-04 por desproporcionada para una prueba de concepto que todavía no tiene
un solo veredicto medido contra datos reales. El código está en el tag
`s02-async`.

`spec-check` no recorre `specs/parked/`. Los casos que sobrevivieron al recorte
conservan su número y su significado en la S02 vigente; el resto queda como
diseño, no como compromiso.

Fases previstas (una spec técnica por vertical, se crean al empezar la fase):

| Fase | Vertical | Spec prevista |
|---|---|---|
| 1 | Caso, identificadores, señales por código y agentes | S02 (implementada) |
| 2 | Fundación de conocimiento y URL a veredicto | S03 conocimiento, S05 dominio, S06 puntuación, S07 veredicto |
| 3 | Registros oficiales: ingesta CNMV e I-SCAN, consulta FCA, cadena de clones | S08 fuentes |
| 4 | Memoria y revisión: grafo compartido de entidades, vínculos `same_actor`, casos previos, revisión del curador (W4, R10, R13, R29) y exploración de la memoria | S09 memoria y revisión |
| 5 | Captura de pantalla y canal Telegram | S10 multimodal, S11 canales |
| — | Documentos PDF y trabajo durable, cuando una entrada deje de caber en la llamada | recuperar `specs/parked/S02-pipeline-asincrono.md` |

## Decisiones de dirección

- Argos debe funcionar completo en local, sin depender de conocimiento remoto.
- El conocimiento curado se versiona en Git y se carga en SurrealDB para que los
  agentes lo consulten localmente.
- Un dashboard local del devcontainer podrá facilitar operación, curación y
  conversación mediante AG-UI. No tiene todavía fase ni spec técnica asignada.
- La infraestructura entra cuando hay código que la usa. Se retiró la que solo
  se sostenía a sí misma; volverá con la entrada que la justifique.
