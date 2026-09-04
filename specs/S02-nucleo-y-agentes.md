# S02 · Núcleo del caso, señales por código y agentes

**Estado**: implementada. Sustituye a la vertical asíncrona, aparcada en
`specs/parked/S02-pipeline-asincrono.md` y conservada en el tag `s02-async`.

Esta vertical convierte la base S01 en un servicio que recibe un aviso, lo
analiza dentro de la llamada y devuelve un veredicto explicado. Cubre W1 y W2;
R1–R9, R15, R16, R28 y R29; y la constitución §3–§8, §11–§12.

Los números `Sxx.n` que sobreviven de la vertical aparcada conservan su
significado y su test. Los que describían el pipeline asíncrono no se reasignan:
la numeración queda discontinua a propósito para que un caso siga apuntando al
mismo comportamiento entre las dos versiones del documento.

## 1. Objetivo y límites

S02 debe proporcionar:

- un gateway HTTP con tres capacidades: analizar un aviso, consultar un caso y
  preguntar sobre su veredicto;
- extracción y normalización de identificadores del aviso por código puro;
- señales decisivas —advertencia oficial vigente y reincidencia confirmada—
  derivadas del catálogo y de la memoria, nunca de un modelo;
- agentes de apoyo que interpretan y redactan, con herramientas acotadas por
  capacidad, tenant y caso;
- un núcleo determinista que puntúa, compone y gobierna las transiciones;
- memoria de entidades compartida entre tenants que solo expone agregados;
- autorización por tenant y errores públicos que no filtran nada.

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
                       POST /v1/notices
                              │
                     ┌────────▼────────┐
                     │  gateway HTTP   │
                     └────────┬────────┘
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
| `argos/api` | Autentica, deriva el tenant y serializa estado |

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

| Capacidad | Ruta | Respuesta |
|---|---|---|
| `analyze_notice` | `POST /v1/notices` | caso y veredicto, o el código del rechazo |
| `get_case` | `GET /v1/cases/{case_id}` | estado, error público y veredicto vigente |
| `ask_case` | `POST /v1/cases/{case_id}/questions` | respuesta apoyada en la evidencia |

El tenant sale siempre de la credencial, nunca del cuerpo. No hay tarjeta de
agente, JSON-RPC ni plano de control: nada de eso tiene consumidor.

## 6. Modelo operacional en SurrealDB

`argos/ops` contiene `tenant`, `case`, `entity`, `entity_link`, `case_entity`,
`warning`, `signal`, `verdict` y la proyección del catálogo. `entity`,
`entity_link` y `warning` son memoria compartida y no llevan `tenant_id`.

`case` guarda el texto del aviso y sus enlaces junto a su hash: sin el texto no
hay análisis, ni explicación, ni forma de reproducir un veredicto. Caduca con el
caso.

Toda transición es una escritura condicional por revisión dentro de una
transacción. Pasar a `analyzing` es lo que impide dos análisis simultáneos.

## 7. Acceso y credenciales

Un usuario de base de datos por workload; hoy solo `gateway`, con rol `EDITOR`.
El usuario `agent` es `VIEWER` y existe para inspección y para el MCP de
SurrealDB. Las identidades del pipeline retirado se eliminan explícitamente.

Las herramientas de un agente comprueban capacidad, tenant y caso antes de leer
nada, y ninguna acepta SurrealQL libre.

## 8. Identificadores por código

Se reconocen dominio, correo, IBAN, teléfono, wallet y arroba con expresiones
regulares y comprobaciones deterministas: dígitos de control del IBAN, dominio
registrable y prefijo telefónico por defecto. Lo reconocido se consume del texto
para que un correo no vuelva a salir como arroba.

Un identificador entra en la memoria compartida ya normalizado. Una empresa la
propone el agente y entra siempre como débil.

## 9. Análisis del aviso

1. El gateway valida el aviso (R1) y deduplica por su hash dentro de la ventana (R9).
2. Abre el caso con su texto, sus identificadores y sus vínculos en una transacción.
3. Marca el caso `analyzing` con escritura condicional.
4. Pregunta a los especialistas y descarta toda señal cuya cita no esté en el aviso.
5. Consulta catálogo y memoria y añade las señales decisivas.
6. `core.score` calcula el nivel; el redactor lo explica sin poder cambiarlo.
7. Cierra el caso con su veredicto versionado.

Un fallo en cualquier punto deja el caso `failed` con un código público, nunca
colgado en `analyzing`.

## 10. Privacidad y retención

El aviso vive en su caso y caduca con él. No se copia a la sesión de Agno, que
guarda referencias. Un tenant recibe de la memoria compartida solo agregados.
Un error público es un código del catálogo; el detalle se queda en el libro.

## 11. Observabilidad

`correlation_id` viaja desde la llamada hasta el veredicto. No hay backend de
trazas desplegado: la observabilidad es el libro y lo que registra LiteLLM.

## 12. Casos anclados

## S02.11 El aviso deja caso e identificadores en una transacción, aplica R1 y deduplica por R9

- Dado un aviso con un correo, su dominio y un teléfono
- Cuando se abre, se repite dentro de la ventana, se envía uno que excede el
  límite de texto y se envía uno de un tenant desconocido
- Entonces el primero deja el caso en `received` con su texto y con una entidad
  y un vínculo por identificador normalizado; el repetido devuelve el mismo caso
  sin escribir; el largo se rechaza con `notice.text_too_long`; y el tenant
  desconocido con `tenant.unknown` (W1.1–3; R1, R2, R9)

## S02.27 El esquema de casos, memoria compartida, señales y veredictos es idempotente

- Dado una SurrealDB con el esquema anterior aplicado
- Cuando `bootstrap-db` se ejecuta dos veces seguidas
- Entonces `argos/ops` contiene `tenant`, `case`, `entity`, `entity_link`,
  `case_entity`, `warning`, `signal` y `verdict`, todas `SCHEMAFULL`; `entity`,
  `entity_link` y `warning` no tienen campo `tenant_id` y `case_entity` sí;
  `case` tiene revisión, `notice_text` y `notice_links`; las tablas del pipeline
  retirado ya no existen; y `schema_version:current` sube a la versión que
  declara `bootstrap-db` (constitución §6, §7; R8, R29)

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

## S02.31 Una herramienta rechaza al agente sin capacidad, a otro tenant y a otro caso

- Dado un caso abierto de un tenant
- Cuando `verdict_writer` pide su contexto, un agente de otro tenant lo pide
  sobre ese caso, y el del propio tenant lo pide sobre un caso inexistente
- Entonces las tres llamadas se rechazan con `tool.not_authorized`,
  `case.not_found` y `case.not_found`; ninguna devuelve contenido y ninguna
  herramienta acepta una consulta SurrealQL libre (R16, R28)

## S02.33 find_entity_history devuelve al otro tenant solo agregados

- Dado dos casos del tenant A sobre el mismo dominio, uno marcado `confirmed`
- Cuando el tenant B pregunta por ese dominio con otra caja y espacios sobrantes
- Entonces recibe el número de casos, la primera y la última vez que se vio y
  que existe una revisión confirmada, sin identificadores de caso, citas ni
  tenants (R29; constitución §6)

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

## S02.38 El gateway deriva el tenant de la identidad y nunca del cuerpo

- Dado un token de servicio de un tenant, uno de curador y ninguno
- Cuando se piden capacidades con cada uno y se envía además un cuerpo que
  declara un tenant distinto del de la credencial
- Entonces sin credencial y con una desconocida la respuesta es 401 sin tocar
  datos; el token de servicio opera siempre sobre su tenant e ignora el del
  cuerpo; y el de curador, que no está atado a un tenant, recibe 403
  (R16; constitución §6)

## S02.40 analyze_notice devuelve el veredicto en la misma llamada

- Dado un aviso breve válido
- Cuando se envía
- Entonces la respuesta trae el caso ya en estado terminal y su veredicto, sin
  trabajo durable de por medio ni espera activa (W1.3; R12, R15; A12)

## S02.42 La API no deja ver el caso de otro tenant

- Dado un caso de un tenant
- Cuando otro tenant lo consulta y hace una pregunta sobre él
- Entonces ambas respuestas son 404 y ninguna revela si el recurso existe
  (R16, R28)

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

## S02.47 El gateway tiene su identidad y la de los agentes es de solo lectura

- Dado el esquema aplicado dos veces
- Cuando el gateway inicia sesión con su usuario, lo intenta con una contraseña
  ajena y el usuario `agent` intenta escribir en `argos/ops`
- Entonces existe el usuario `gateway` y entra solo con su contraseña; los
  usuarios del pipeline retirado y el compartido `ledger` ya no existen; y
  `agent` lee pero no escribe: su `CREATE` no deja fila y su intento de definir
  un usuario se rechaza por permisos (constitución §6, §7; R16)

Un usuario `VIEWER` de SurrealDB no rechaza una escritura de datos: la ejecuta
sin efecto y responde `OK` con resultado vacío. Solo el DDL da error explícito.
Por eso la comprobación mira la fila, no el código de respuesta.

## S02.51 El error público no filtra claves, SQL ni texto del aviso

- Dado un fallo interno cuyo detalle contiene una consulta SurrealQL, una
  contraseña y un fragmento del aviso
- Cuando el caso lo registra y el cliente consulta su estado
- Entonces el error público es un código estable del catálogo, el interno queda
  en el libro para el curador y la respuesta pública no contiene la consulta, la
  contraseña ni el texto (R28; constitución §11)

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
