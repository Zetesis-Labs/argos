# S02 · Núcleo del caso, señales por código y agentes

**Estado**: implementada. Sustituye a la vertical asíncrona, aparcada en
`specs/parked/S02-pipeline-asincrono.md` y conservada en el tag `s02-async`.

Esta vertical convierte la base S01 en una herramienta local que recibe un
aviso, lo analiza dentro de la llamada y devuelve un veredicto explicado. Cubre
W1, W2 y W4; R1–R9, R13, R15 y R29; y la constitución §3–§8, §11–§12.

Argos se distribuye como un devcontainer que alguien se baja y ejecuta en su
máquina. Hay un solo usuario: quien lo arranca. No hay tenants, no hay
credenciales, no hay roles y no hay errores que ocultar a un llamante.

Los números `Sxx.n` que sobreviven de la vertical aparcada conservan su
significado y su test. Los que describían el pipeline asíncrono no se reasignan:
la numeración queda discontinua a propósito para que un caso siga apuntando al
mismo comportamiento entre las dos versiones del documento.

## 1. Objetivo y límites

S02 debe proporcionar:

- una CLI como superficie principal y una API HTTP local debajo, con cuatro
  capacidades: analizar un aviso, consultar un caso con su evidencia, preguntar
  sobre su veredicto y marcarlo confirmado o falso positivo;
- extracción y normalización de identificadores del aviso por código puro;
- señales decisivas —advertencia oficial vigente y reincidencia confirmada—
  derivadas del catálogo y de la memoria, nunca de un modelo;
- agentes de apoyo que interpretan y redactan, con herramientas acotadas por
  capacidad, tenant y caso;
- un núcleo determinista que puntúa, compone y gobierna las transiciones;
- memoria de entidades entre casos que expone agregados;
- un fallo que deja el caso operable con su error real y un presupuesto de
  tiempo que se cumple.

S02 **no** implementa documentos, cola durable, almacén de objetos, ingesta de
fuentes oficiales, revisión del curador ni análisis de dominio. Un aviso que
solo trae imagen se acepta, se deduplica por su hash y termina `insufficient`:
Argos no lee imágenes todavía.

Consecuencia de no haber revisión del curador: todo caso nace `unreviewed` y
nada lo cambia, así que `history.confirmed` nunca es cierto y la señal de
reincidencia no puede dispararse en producción. Su cálculo está implementado y
probado (S02.28, S02.57), pero el único camino vivo a `critical` es hoy la
advertencia oficial vigente. Lo desbloquea la vertical de revisión.

## 2. Topología

```text
            argos analyze "…"        POST /v1/notices
                     │                      │
                     └──────────┬───────────┘
                          ┌─────▼─────┐
                          │  cableado │
                          └─────┬─────┘
                              │
              ┌───────────────▼───────────────┐
              │ core: identificadores, score, │
              │ composición del veredicto     │
              └───────┬───────────────┬───────┘
                      │               │ prompts
                ┌─────▼──────┐   ┌────▼──────────────────┐
                │ SurrealDB  │   │ triage / patterns /   │
                │ argos/ops  │   │ writer / conversation │
                └─────┬──────┘   └───────────────────────┘
                      │
             proyección del catálogo OKF (S03)
```

Un solo proceso de larga vida. Sin bus, sin almacén de objetos, sin AgentOS y
sin equipo de Agno: coordinar dos especialistas es un bucle.

## 3. Responsabilidades por componente

| Componente | Cometido |
|---|---|
| `argos/core` | Identificadores, señales, puntuación, planes del libro. Sin I/O |
| `argos/usecases` | Abre el caso, reúne señales, cierra el veredicto, responde |
| `argos/agents` | Declara los agentes y sus herramientas |
| `argos/platform` | SurrealDB, LiteLLM, reloj, identificadores, catálogo |
| `argos/api` | Serializa estado sobre HTTP |
| `argos/cli` | Superficie principal: analiza, muestra, pregunta y revisa |
| `argos/wiring` | Cableado único que comparten la CLI y el API |

## 4. Catálogo de agentes

| Agente | Cometido | Capacidades |
|---|---|---|
| `triage_agent` | Interpreta el aviso y propone tipologías | contexto, registros, historia |
| `patterns_agent` | Detecta manipulación citando el aviso | contexto |
| `verdict_writer` | Explica un nivel ya calculado | ninguna |
| `conversation_agent` | Responde sobre un veredicto emitido | contexto, historia |

Ningún agente marca `official` ni `recidivism`: el parser del informe descarta
esos campos. Un agente consulta el catálogo para razonar; la señal decisiva la
escribe el código tras consultarlo él mismo.

## 5. Frontera de la API

| Capacidad | CLI | Ruta | Respuesta |
|---|---|---|---|
| `analyze_notice` | `argos analyze` | `POST /v1/notices` | caso, veredicto y evidencia |
| `get_case` | `argos show` | `GET /v1/cases/{case_id}` | estado, error y veredicto vigente |
| `ask_case` | `argos ask` | `POST /v1/cases/{case_id}/questions` | respuesta apoyada en la evidencia |
| `review_case` | `argos review` | `POST /v1/cases/{case_id}/review` | la marca aplicada |

No hay autenticación: el proceso corre en el devcontainer de quien lo usa y
publica su puerto solo en loopback. Tampoco hay tarjeta de agente, JSON-RPC ni
plano de control: nada de eso tiene consumidor.

`/health` responde `ok` solo si el libro contesta. Un proceso vivo con la base
caída responde 503.

## 6. Modelo operacional en SurrealDB

`argos/ops` contiene `case`, `entity`, `entity_link`, `case_entity`, `warning`,
`signal`, `verdict` y la proyección del catálogo. Ninguna fila lleva `tenant_id`:
todo pertenece a quien ejecuta Argos.

`case` guarda el texto del aviso y sus enlaces: sin el texto no hay análisis, ni
explicación, ni forma de reproducir un veredicto. No caduca: un análisis local se
guarda hasta que su dueño lo borra.

`entity`, `entity_link` y `warning` sobreviven al caso que los descubrió. El
vínculo `case_entity` es lo que permite ver una reincidencia.

Toda transición es una escritura condicional por revisión dentro de una
transacción. Pasar a `analyzing` es lo que impide dos análisis simultáneos.

Un aviso repetido abre un caso nuevo. Deduplicar por hash ahorraría un análisis
y a cambio devolvería un caso muerto durante su ventana: en local no hay coste
que racionar.

## 7. Acceso y credenciales

No hay credenciales de usuario. Lo que sí hay es mínimo privilegio dentro de
SurrealDB: el proceso entra como `gateway` con rol `EDITOR` y el usuario `agent`
es `VIEWER`, para inspección y para el MCP. Root se reserva al bootstrap. Las
identidades del pipeline retirado se eliminan explícitamente.

Las herramientas de un agente comprueban capacidad y caso antes de leer nada, y
ninguna acepta SurrealQL libre.

## 8. Identificadores por código

Se reconocen dominio, correo, IBAN, teléfono, wallet y arroba con expresiones
regulares y comprobaciones deterministas: dígitos de control del IBAN, dominio
registrable y prefijo telefónico por defecto. Lo reconocido se consume del texto
para que un correo no vuelva a salir como arroba.

Un identificador entra en la memoria compartida ya normalizado. Una empresa la
propone el agente y entra siempre como débil.

## 9. Análisis del aviso

1. Valida el aviso (R1).
2. Abre el caso con su texto, sus identificadores y sus vínculos en una transacción.
3. Marca el caso `analyzing` con escritura condicional.
4. Pregunta a los especialistas y descarta toda señal cuya cita no esté en el aviso.
5. Consulta catálogo y memoria y añade las señales decisivas.
6. `core.score` calcula el nivel; el redactor lo explica sin poder cambiarlo.
7. Cierra el caso con su veredicto versionado.

Todo ello dentro del presupuesto de R15. Agotarlo, o cualquier otro fallo, deja
el caso `failed` con su error, nunca colgado en `analyzing`.

Después, quien lo usa puede marcar el caso confirmado o falso positivo (W4,
R13). Esa marca es la única fuente de la reincidencia: sin ella la memoria sabe
que un identificador se repite, pero no que alguna vez fue fraude.

## 10. Privacidad y ciclo de vida

Los datos no salen de la máquina de quien ejecuta Argos. El aviso vive en su
caso y no se copia a la sesión de Agno, que guarda referencias. La memoria
devuelve agregados porque es lo útil, no porque haya de quién protegerlos.

Nada caduca solo. Borrar un caso borra sus vínculos, sus señales y sus
veredictos, y deja intactas las entidades: son memoria entre casos.

## 11. Observabilidad

No hay backend de trazas: la observabilidad es el libro, la salida de la CLI y
lo que registra LiteLLM. Un error se muestra tal cual, con su tipo y su mensaje:
el destinatario es quien ejecuta Argos.

## 12. Casos anclados

## S02.11 El aviso deja caso e identificadores en una transacción y aplica R1

- Dado un aviso con un correo, su dominio y un teléfono
- Cuando se abre, se repite, se envía uno que excede el límite de texto y se
  envía uno vacío
- Entonces el primero deja el caso en `received` con su texto y con una entidad
  y un vínculo por identificador normalizado; el repetido abre un caso nuevo en
  lugar de devolver el anterior; el largo se rechaza con `notice.text_too_long`;
  y el vacío con `notice.empty` (W1.1–3; R1, R2)

## S02.27 El esquema de casos, memoria compartida, señales y veredictos es idempotente

- Dado una SurrealDB con el esquema anterior aplicado
- Cuando `bootstrap-db` se ejecuta dos veces seguidas
- Entonces `argos/ops` contiene `case`, `entity`, `entity_link`, `case_entity`,
  `warning`, `signal` y `verdict`, todas `SCHEMAFULL`; ninguna tiene campo
  `tenant_id` y solo `case_entity`, `signal` y `verdict` llevan `case_id`; `case`
  tiene `notice_text`, `notice_links`, `review_state` y `error`, y ya no tiene
  `expires_at`; las tablas del pipeline retirado y `tenant` ya no existen; y
  `schema_version:current` sube a la versión que declara `bootstrap-db`
  (constitución §6, §7; R8, R29)

## S02.28 El núcleo determinista calcula el nivel conforme a R4

- Dado conjuntos de señales con evidencia
- Cuando `core.score` los puntúa con todos los análisis respondidos
- Entonces una coincidencia oficial vigente da `critical`; una reincidencia
  fuerte de un caso `confirmed` da `critical`; dos señales fuertes de análisis
  distintos dan `high`; dos fuertes del mismo análisis dan `medium`; una fuerte
  da `medium`; tres débiles dan `medium`; dos débiles dan `low`; y ninguna señal
  da `low` (R4)

## S02.29 Una señal sin evidencia no puntúa y un parcial nunca es low

- Dado una señal sin fuente, otra sin fecha de observación y otra sin cita
- Cuando se filtran antes de puntuar y después se puntúa un caso degradado
- Entonces las tres se descartan; el mismo conjunto que daba `low` da `medium`
  al estar degradado; un caso degradado sin ninguna señal da `undetermined`; y
  todo nivel, `undetermined` incluido, trae acciones no vacías (R3, R5, R7)

## S02.30 Cada agente declara solo sus herramientas y ninguna escribe en el libro

- Dado el catálogo de agentes
- Cuando se leen sus capacidades
- Entonces están los cuatro agentes de la constitución §8; el catálogo entero de
  capacidades es de lectura; `verdict_writer` no tiene ninguna; y
  `conversation_agent` no puede consultar registros oficiales
  (constitución §4, §8; R16)

## S02.31 Una herramienta rechaza al agente sin capacidad y al caso inexistente

- Dado un caso abierto
- Cuando `verdict_writer` pide su contexto, `triage_agent` lo pide sobre un caso
  inexistente y lo pide sobre el suyo
- Entonces la primera se rechaza con `tool.not_authorized`, la segunda con
  `case.not_found`, la tercera devuelve el contexto, y ninguna herramienta
  acepta una consulta SurrealQL libre (constitución §7)

## S02.33 find_entity_history devuelve agregados de la memoria, no los casos

- Dado dos casos sobre el mismo dominio, uno marcado `confirmed`
- Cuando se pregunta por ese dominio con otra caja y espacios sobrantes, y
  después por uno que nadie ha visto
- Entonces el primero devuelve el número de casos, la primera y la última vez
  que se vio y que existe una revisión confirmada, sin identificadores de caso
  ni citas; y el desconocido devuelve un agregado vacío, no un error
  (R29; constitución §6)

## S02.35 El análisis pasa el caso a analyzing y lo cierra con su veredicto versionado

- Dado un caso abierto y un investigador que devuelve una señal fuerte cuya cita
  está en el aviso
- Cuando se analiza
- Entonces el caso pasa por `analyzing` y termina `verdict_issued` con un
  veredicto versión 1, sus acciones y su señal con evidencia persistida
  (W1.4–8; R4, R8, R12)

## S02.36 Un aviso sin entrada analizable termina insufficient

- Dado un aviso que solo trae una imagen
- Cuando se analiza
- Entonces el caso termina `insufficient`, sin nivel de riesgo fingido, con
  `undetermined` y con acciones que piden una entrada más completa
  (W1 caminos alternativos; R5, R12)

## S02.37 El clúster real analiza con el modelo mock sin dejar el aviso en la sesión

- Dado un aviso con un marcador único y los agentes reales sobre el modelo `mock`
- Cuando se analiza el caso
- Entonces termina con un veredicto y sus acciones; el informe del modelo `mock`
  no cumple el contrato, así que no produce ninguna señal; el prompt sí contiene
  el aviso, porque sin él no hay análisis; y la sesión de Agno no lo guarda
  (constitución §6, §11; R8)

## S02.40 analyze_notice devuelve el veredicto y su evidencia en la misma llamada

- Dado un aviso breve válido y un investigador que cita el aviso
- Cuando se envía
- Entonces la respuesta trae el caso en estado terminal, su veredicto y las
  señales que lo sostienen con su cita, sin trabajo durable ni espera activa
  (W1.3; R12, R15; A12)

## S02.42 La API local sirve el caso con su evidencia y responde 404 al inexistente

- Dado la API local sin credencial alguna
- Cuando se analiza un aviso, se consulta su caso, se consulta uno inexistente y
  se pregunta por él, y se consulta la salud
- Entonces el análisis responde 200 con sus señales; el caso se sirve con su
  evidencia; el inexistente responde 404 en ambas rutas; y `/health` responde
  `ok` porque el libro contesta (constitución §12)

## S02.43 ask_case responde con la evidencia persistida y no muta el veredicto

- Dado un caso con veredicto emitido y sus señales
- Cuando un actor autorizado pregunta por él
- Entonces recibe una respuesta apoyada en el veredicto, el veredicto sigue en
  su versión y nivel, no se crean señales nuevas y un caso sin veredicto
  responde que todavía no lo hay (W2, R8)

## S02.45 Un caso ya cerrado no se vuelve a analizar ni duplica su veredicto

- Dado un caso que ya terminó su análisis
- Cuando se pide analizarlo otra vez
- Entonces la petición se descarta por estado y el caso conserva un único
  veredicto de versión 1 (R12, R25)

## S02.47 El proceso entra con su identidad y la de los agentes es de solo lectura

- Dado el esquema aplicado dos veces
- Cuando el proceso inicia sesión con su usuario, lo intenta con una contraseña
  incorrecta y el usuario `agent` intenta escribir en `argos/ops`
- Entonces existe el usuario `gateway` y entra solo con su contraseña; los
  usuarios del pipeline retirado y el compartido `ledger` ya no existen; y
  `agent` lee pero no escribe: su `CREATE` no deja fila y su intento de definir
  un usuario se rechaza por permisos (constitución §6, §7)

Un usuario `VIEWER` de SurrealDB no rechaza una escritura de datos: la ejecuta
sin efecto y responde `OK` con resultado vacío. Solo el DDL da error explícito.
Por eso la comprobación mira la fila, no el código de respuesta.

## S02.51 Un análisis que falla deja el caso operable y lo dice

- Dado un investigador que revienta a mitad del análisis
- Cuando se analiza un caso y después se consulta, y aparte se envía un aviso
  que no supera el límite de texto
- Entonces el caso termina `failed` con el error real —tipo y mensaje— visible
  en su consulta y no colgado en `analyzing`; y el aviso rechazado responde 422
  con el código de su límite (R5; constitución §11)

## S02.54 El catálogo sintético demuestra la consulta de registros

- Dado el catálogo de demostración exclusivamente sintético con identificadores
  estables, regulador, URL de origen, entidad, estado y fecha de captura
- Cuando sus advertencias se proyectan y se consulta un dominio incluido
- Entonces devuelve la coincidencia vigente con su regulador, URL y fecha y no
  presenta como vigente una advertencia retirada (R3, R11, R17; constitución
  §6, §13)

## S02.55 El perfil de servicios prepara y ejecuta Argos sin pasos manuales

- Dado un checkout del repositorio sin `.devcontainer/.env` y con los volúmenes
  locales vacíos
- Cuando el operador ejecuta `docker compose -f .devcontainer/docker-compose.yml
  --profile services up -d --build`
- Entonces una preparación idempotente aplica el esquema y proyecta el
  conocimiento sintético versionado antes de arrancar el gateway como único
  proceso; el compose no declara ningún servicio del pipeline retirado; el
  gateway usa el modelo `mock` por defecto y su único puerto publicado escucha
  en loopback; y una SurrealDB de test evita que ese proceso interfiera con
  `pytest` en el mismo devcontainer (constitución §11–§12)

## S02.56 Los identificadores del aviso se extraen y normalizan sin modelo

- Dado un aviso con un IBAN válido, otro con dígitos de control incorrectos, una
  URL con subdominio y mayúsculas, un correo, una arroba, una wallet y un
  teléfono con espacios
- Cuando el núcleo extrae sus identificadores
- Entonces el IBAN válido entra compactado y el inválido no entra; el dominio
  entra como dominio registrable en minúsculas y también el del correo; la
  arroba entra como débil; el teléfono entra en forma E.164; y un enlace sin
  texto también aporta su dominio (R2; constitución §3, §4)

## S02.57 La advertencia oficial y la reincidencia solo las marca el código

- Dado un informe de un modelo que se declara oficial y reincidente sobre un
  dominio, y una advertencia vigente real sobre otro dominio del catálogo
- Cuando se analiza el aviso que cita el dominio advertido
- Entonces el informe del modelo no produce ninguna señal; la señal oficial la
  escribe el código con la URL y la fecha del catálogo; el veredicto es
  `critical`; y no aparece ninguna señal de reincidencia sin caso previo
  confirmado (R4; constitución §4)

## S02.58 Una cita que no aparece en el aviso no sostiene su señal

- Dado dos señales del mismo análisis, una con una cita literal del aviso y otra
  con una cita inventada
- Cuando se filtran antes de puntuar
- Entonces solo sobrevive la que cita el aviso (R3; constitución §4)

## S02.59 La CLI analiza un aviso y escribe el veredicto con su evidencia

- Dado `argos analyze` con el texto de un aviso y un enlace
- Cuando se ejecuta con un investigador que cita el aviso
- Entonces la orden acepta texto y enlaces repetibles; la salida trae el nivel en
  palabras, la explicación, cada indicio con su cita y su fuente, y las acciones
  recomendadas; y termina con código de salida 0 (W1; constitución §12)

## S02.60 Marcar un caso confirmado hace visible la reincidencia en el siguiente

- Dado un caso ya analizado sobre un identificador y su marca de confirmado
- Cuando llega un aviso nuevo que cita el mismo identificador
- Entonces el segundo caso recibe una señal de reincidencia escrita por el
  código, con el número de casos previos, y su nivel es `critical`; sin la marca
  esa señal no existe (W4; R4, R13, R29)

## S02.61 La salida del caso enseña el nivel, la evidencia y qué hacer

- Dado un caso con veredicto `critical` sostenido por una advertencia oficial
- Cuando se representa para la terminal
- Entonces muestra el nivel en palabras, marca el indicio como oficial, incluye
  su fuente y lista las acciones recomendadas (R6, R7)

## S02.62 Un análisis que agota el presupuesto termina failed, no colgado

- Dado un investigador que tarda más que el presupuesto de R15
- Cuando se analiza un caso
- Entonces el análisis se corta al agotarlo, el caso termina `failed` con el
  motivo y nunca queda en `analyzing` (R15; constitución §4)
