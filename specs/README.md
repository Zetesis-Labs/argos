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
| [S01](S01-plataforma.md) | Base verificada: SurrealDB/MCP, LiteLLM y Langfuse | 0 | constitución §2, §7, §11–§12 |
| [S02](S02-agentos-workers.md) | AgentOS, A2A, NATS, RustFS y worker de documentos | 1 | W1, W2, W5; R1, R8, R9, R12, R15–R29; constitución §3–§12 |

S01 y S02 están implementadas y verificadas. S02 tiene 55 casos anclados; los
últimos cargan advertencias exclusivamente sintéticas para demostrar la consulta
de registros sin adelantar la ingesta real de S07 y arrancan todos los procesos
desde el perfil local `services`.

Fases previstas (una spec técnica por vertical, se crean al empezar la fase):

| Fase | Vertical | Spec prevista |
|---|---|---|
| 1 | AgentOS y clúster de agentes: A2A, NATS, RustFS y worker de documentos | S02 (implementada) |
| 2 | URL a veredicto: análisis de dominio, puntuación, redactor, servicio | S03 identificadores, S04 dominio, S05 puntuación, S06 veredicto |
| 3 | Registros oficiales: ingesta CNMV e I-SCAN, consulta FCA, cadena de clones | S07 fuentes |
| 4 | Memoria y revisión: grafo compartido de entidades, vínculos `same_actor`, casos previos, revisión del curador (W4, R10, R13, R29) y exploración de la memoria | S08 memoria y revisión |
| 5 | Captura de pantalla y canal Telegram | S09 multimodal, S10 canales |

## Decisiones de dirección

- Argos debe funcionar completo en local, sin depender de conocimiento remoto.
- El conocimiento curado se versiona en Git y se carga en SurrealDB para que los
  agentes lo consulten localmente.
- Un dashboard local del devcontainer podrá facilitar operación, curación y
  conversación mediante AG-UI. No tiene todavía fase ni spec técnica asignada.
