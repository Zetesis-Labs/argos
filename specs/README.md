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

| Spec | Vertical | Cubre |
|---|---|---|
| [funcional](argos/veredicto/functional-specs.md) | Veredicto de avisos y documentos (iniciativa v1) | W1–W5, R1–R29 |
| [conocimiento](argos/conocimiento/functional-specs.md) | Catálogo curado, exploración y proyección local | W1–W4, R1–R14 |
| [S01](S01-plataforma.md) | Base verificada: SurrealDB/MCP y LiteLLM | constitución §2, §7, §11–§12 |
| [S02](S02-nucleo-y-agentes.md) | Caso, identificadores, señales por código, agentes y gateway | W1, W2; R1–R9, R15, R16, R28, R29; constitución §3–§8, §11–§12 |
| [S03](S03-conocimiento-okf.md) | Fundación OKF en Git, explorador y proyección SurrealDB | conocimiento W1–W3, R1–R12 y R14 |

S01, S02 y S03 están implementadas y verificadas. W5 —documentos— pertenece al
producto pero no al alcance actual: su diseño está aparcado.

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

## Lo que el recorte absorbió

Tres verticales previstas dejaron de existir como tales porque su contenido
entró en S02 al hacerla honesta:

| Vertical prevista | Dónde está ahora |
|---|---|
| S04 identificadores | S02.56: extracción y normalización por código puro |
| S07 veredicto | S02.28, S02.29, S02.35 y S02.36: nivel, degradación, acciones y cierre |
| parte de S09 memoria | S02.33 y S02.57: entidad compartida, agregados y señal de reincidencia |

Reconocer un IBAN o componer un veredicto no necesitaba una fase propia. Lo que
sí la necesita es medir si la escalera acierta.

## Lo que queda, por lo que de verdad bloquea

El orden ya no es el del plan original. Está puesto por qué impide hoy que Argos
emita un veredicto útil, no por dependencia técnica.

| Orden | Vertical | Spec | Por qué va aquí |
|---|---|---|---|
| 1 | Fuentes oficiales: ingesta CNMV e I-SCAN, consulta FCA, cadena de clones | S08 | El catálogo tiene tres advertencias sintéticas. Un aviso que no cite `example-broker.test` sale `undetermined` o `low`, y ninguna otra vertical arregla eso |
| 2 | Revisión del curador: marcar confirmado y falso positivo (W4, R13) | S09a | Nada escribe `review_state`. Sin ella `history.confirmed` nunca es cierto y la reincidencia, uno de los dos caminos a `critical`, es inalcanzable |
| 3 | Calibración de R4 contra avisos reales etiquetados | S06 | La escalera se diseñó sobre el papel y no se ha medido nunca. Sin 1 y 2 no hay nada que medir |
| 4 | Dominio: registro, certificado, reputación y parecido con marcas | S05 | Aporta señales propias; hasta ahora solo hay triaje y patrones, y dos señales fuertes del mismo análisis no pasan de `medium` |
| 5 | Vínculos `same_actor` y exploración de la memoria (R10, R29) | S09b | Amplía la reincidencia más allá del identificador exacto |
| 6 | Captura de pantalla y canal Telegram | S10, S11 | Entrada nueva, no mejor veredicto |
| — | Documentos PDF y trabajo durable | recuperar `specs/parked/S02-pipeline-asincrono.md` | Cuando una entrada deje de caber en la llamada |

Las fases numeradas del plan anterior desaparecen: eran cinco tramos fijos y el
recorte demostró que el orden depende de lo que falte medir, no de lo que falte
construir.

## Decisiones de dirección

- Argos debe funcionar completo en local, sin depender de conocimiento remoto.
- El conocimiento curado se versiona en Git y se carga en SurrealDB para que los
  agentes lo consulten localmente.
- Un dashboard local del devcontainer podrá facilitar operación, curación y
  conversación mediante AG-UI. No tiene todavía fase ni spec técnica asignada.
- La infraestructura entra cuando hay código que la usa. Se retiró la que solo
  se sostenía a sí misma; volverá con la entrada que la justifique.
