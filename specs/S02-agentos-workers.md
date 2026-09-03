# S02 · AgentOS, clúster de agentes y workers

**Estado**: en implementación. Los casos anclados están en el §16; los
criterios del §15 que aún no tienen caso son trabajo pendiente.

Esta vertical convierte la base S01 en un AgentOS capaz de coordinar agentes
especialistas y trabajos asíncronos. Cubre W1, W2 y W5; R1, R8, R9, R12,
R15–R29; y la constitución §3–§4, §6–§12.

La numeración `S02.n` se asigna junto con los tests, en el orden de
implantación del §14. Un criterio del §15 sin caso numerado no se considera
implementado.

## 1. Objetivo y límites

S02 debe proporcionar:

- un AgentOS con gateway, agentes, equipo y workflow declarados;
- acceso de los agentes a la memoria operacional mediante MCP acotado;
- capacidades remotas estables por A2A sin publicar especialistas internos;
- un libro durable de trabajos y un outbox en SurrealDB;
- comandos y eventos mediante NATS JetStream;
- artefactos privados en RustFS mediante un puerto S3-compatible;
- un worker stateless que extraiga texto y OCR de PDFs;
- reanudación del caso desde estado durable tras reinicios;
- autorización por tenant, idempotencia y observabilidad extremo a extremo.

S02 no implementa todavía las fuentes oficiales ni el cálculo completo de
riesgo. Los especialistas pueden operar con fakes para demostrar coordinación,
permisos y persistencia. Tampoco crea web pública, Telegram, WhatsApp, audio,
vídeo ni interpretación jurídica de contratos.

## 2. Topología objetivo

```text
                         API / A2A
                            │
                    ┌───────▼────────┐
                    │ AgentOS gateway│
                    └───────┬────────┘
                            │
           ┌────────────────▼────────────────┐
           │ verdict_workflow / investigation│
           │ Team + especialistas Agno       │
           └───────┬───────────────┬─────────┘
                   │ MCP           │ crear/consultar trabajo
             ┌─────▼──────┐        │
             │ SurrealDB  │◄───────┘
             │ argos/ops  │── outbox dispatcher ──► NATS JOBS/EVENTS
             └─────┬──────┘                              │
                   │                                     ▼
              refs/chunks                      document worker
                   │                           │          │
             ┌─────▼──────┐              estado/refs     │ objetos
             │ Agno DB    │                    │          ▼
             │ sessions   │              SurrealDB ◄── RustFS
             └────────────┘                    │
                                      evento en outbox
                                               │
                                      workflow resumer
```

Postgres, ClickHouse, Redis y el backend de objetos privado que use Langfuse
son dependencias de observabilidad, no componentes del estado de Argos.

## 3. Responsabilidades por componente

| Componente | Responsabilidad | No puede hacer |
|---|---|---|
| **AgentOS gateway** | Autenticar, resolver tenant, exponer capacidades API/A2A y devolver estados | Exponer credenciales internas o mantener abierta una llamada por un trabajo largo |
| **verdict_workflow** | Aplicar transiciones, presupuesto temporal, paralelismo, degradación y cierre | Delegar reglas de negocio a un prompt |
| **investigation_team** | Coordinar especialistas de investigación | Puntuar fuera de `core.score` |
| **Agentes especialistas** | Interpretar su dominio y usar herramientas acotadas | Consultar libremente toda la base o escribir fuera de su cometido |
| **MCP de operaciones** | Ofrecer herramientas por capacidad, tenant y caso | Entregar SurrealQL general al agente de producto |
| **SurrealDB argos/ops** | Ser verdad de casos, grafo, trabajos, intentos, chunks y outbox | Guardar PDFs o extracciones completas |
| **SurrealDB agno/sessions** | Persistir sesiones y memoria propia del runtime | Ser verdad operacional del caso |
| **Outbox dispatcher** | Publicar comandos y eventos confirmados, marcar su entrega y recuperar arrendamientos vencidos de intentos | Cerrar trabajos con resultados propios: solo aplica las transiciones deterministas de R21 |
| **NATS JetStream** | Entregar comandos y eventos referenciados | Ser la única copia del estado o transportar documentos |
| **Document worker** | Reclamar, verificar, extraer, persistir y anunciar | Conversar, puntuar o conservar estado local durable |
| **RustFS** | Guardar originales y derivados privados | Resolver autorización de negocio sin SurrealDB |
| **Workflow resumer** | Consumir eventos de documento, releer estado y crear el trabajo de análisis cuando el caso no tiene más documentos pendientes | Confiar en el contenido del evento como fuente de verdad o ejecutar el análisis él mismo |
| **Case analyzer** (`case-analyzer-v1`) | Consumir `case.analyze`, reclamar el intento y ejecutar `verdict_workflow` hasta un estado terminal | Mover el caso sin un intento reclamado |
| **Janitor** | Borrar artefactos `uploading` caducados y ejecutar la retención por referencias | Borrar por prefijo o sin comprobar referencias vivas |

## 4. Catálogo inicial de agentes

| Agente | Entrada | Herramientas | Salida |
|---|---|---|---|
| `triage_agent` | Aviso o chunks autorizados | normalización y clasificación | identificadores, idioma y tipologías candidatas |
| `registries_agent` | Entidades normalizadas | consulta de advertencias | señales de registros oficiales |
| `domain_agent` | Dominios | RDAP, certificados y reputación | señales técnicas fechadas |
| `patterns_agent` | Texto autorizado | catálogo de patrones | señales con cita y posición |
| `memory_agent` | Identificadores y tenant | consultas MCP de reincidencia | apariciones y revisiones previas |
| `document_agent` | Caso y documento | consulta de trabajos, manifiesto y chunks autorizados | estado, estructura del documento y selección de fragmentos para otros especialistas; nunca crea ni reprocesa trabajos |
| `verdict_writer` | nivel calculado y señales | guía de acciones | explicación; no puede modificar el nivel |
| `conversation_agent` | pregunta, caso y sesión | lectura de veredicto y memoria autorizada | respuesta sin mutar señales ni nivel |

`investigation_team` agrupa triaje, registros, dominio, patrones y memoria.
`verdict_workflow` es el único coordinador autorizado para mover el caso entre
estados y emitir un veredicto.

## 5. Frontera API y A2A

El gateway publica capacidades, no la topología interna:

| Capacidad | Tipo | Respuesta |
|---|---|---|
| `analyze_notice` | síncrona con límite | caso y veredicto o estado parcial; si el presupuesto vence sin estado terminal, el caso en curso |
| `submit_document` | asíncrona | caso, documento, trabajo (nuevo o existente) y su estado |
| `get_job` | consulta | estado público, intento y referencias disponibles |
| `get_case` | consulta | estado y veredicto cuando existe |
| `ask_case` | sesión | respuesta apoyada en evidencia persistida |
| `reprocess_document` | curador | nuevo trabajo versionado |

La coordinación interna de una instancia usa Team y Workflow de Agno. A2A se
usa entre despliegues AgentOS o por clientes remotos. Cada petición remota lleva
identidad de servicio y correlación; el gateway deriva el tenant y no acepta un
`tenant_id` confiando únicamente en el cuerpo.

Los agentes especialistas y workers no publican Agent Card ni endpoint A2A. Un
trabajo largo finaliza la llamada de envío tras su aceptación. El cliente usa
`get_job`, `get_case` o una notificación referenciada.

`analyze_notice` no ejecuta el análisis dentro de la llamada: valida R1, aplica
R9, crea caso, trabajo `case.analyze` y comando de outbox en una transacción y
espera el estado terminal hasta el presupuesto de R15. Si el proceso que
atendía la llamada muere, el intento vence, se reencola y el cliente recupera
el caso con `get_case`. La creación y el reproceso de trabajos son casos de uso
del gateway, nunca herramientas de un agente.

## 6. Modelo operacional en SurrealDB

La jerarquía obligatoria es:

```text
tenant → case → document → extraction → chunk
                  └──────→ job → attempt
                  └──────→ verdict (versionado)
case → signal/evidence
case → entity ← case de otro tenant      (la entidad es compartida)
entity ↔ entity                          (same_actor)
transaction → outbox_entry
```

Reglas del modelo:

- toda fila de caso, documento, trabajo, intento, extracción, chunk, señal,
  veredicto y revisión lleva tenant y las consultas lo filtran antes de
  devolver contenido; las entidades, sus vínculos y las advertencias oficiales
  son compartidas y a un tenant solo se le devuelven agregados (R29);
- un documento pertenece a un caso y una extracción a un documento; el mismo
  hash en el mismo caso es el mismo documento, y en otro caso es otro documento
  (R22);
- el trabajo guarda tipo, versión, opciones normalizadas, estado, intento actual,
  máximo de intentos, arrendamiento (`lease_until`), error público, error
  interno y correlación de traza;
- cada intento conserva tiempos, consumidor, arrendamiento y resultado para
  auditar entregas repetidas; su comando de outbox lleva `not_before` para el
  backoff;
- el caso conserva su veredicto vigente y las versiones superadas por un
  reproceso;
- los chunks conservan extracción, página, orden, rango y hash;
- el outbox nace en la misma transacción que el trabajo o cambio que anuncia;
- el evento recibido nunca mueve estados sin comparar primero la versión y el
  estado actual en base de datos;
- el optimismo usa revisión o transición condicional para impedir dos cierres
  incompatibles del mismo trabajo.

`agno/sessions` guarda únicamente el estado del runtime. Una sesión puede citar
identificadores operacionales, pero eliminarla no elimina el caso y perderla no
impide reanudarlo.

## 7. Acceso y credenciales

- Cada workload de producción tiene identidad distinta: gateway, dispatcher,
  resumer, worker y cada clase de agente que necesite permisos diferentes.
- Los agentes llaman herramientas MCP de negocio (`get_case_context`,
  `find_registry_matches`, `find_entity_history`, `get_document_job`,
  `get_extraction_manifest`, `get_extraction_chunks`); no reciben la herramienta
  de consulta general como interfaz normal de producto ni ninguna que cree,
  reprocese o cierre trabajos.
- El MCP valida identidad, tenant, capacidad, caso y campos de salida.
  `find_entity_history` devuelve a un agente que trabaja para un tenant solo
  agregados de la entidad (R29).
- `get_extraction_chunks` entrega como máximo el presupuesto de chunks que fija
  el workflow por llamada, y el runtime no persiste en `agno/sessions` los
  mensajes de herramienta que los transportan: la sesión guarda `extraction_id`
  y los identificadores de chunk.
- El worker puede usar un adaptador directo tipado para transacciones y leases,
  con permisos sobre trabajos, intentos, documentos y extracciones; no puede
  leer sesiones ni revisar casos.
- Los agentes no reciben claves de RustFS. Gateway y worker usan credenciales
  distintas o URLs firmadas breves con operación, objeto y caducidad acotados.
- El usuario genérico `agent` de S01 es una facilidad de desarrollo y debe
  dividirse antes de un despliegue productivo de S02.
- Identidades de producción: gateway, dispatcher, resumer, case analyzer,
  worker, janitor y cada clase de agente con permisos distintos.

## 8. Contrato NATS JetStream

### Streams y subjects

| Stream | Subjects iniciales | Productor | Consumidor durable |
|---|---|---|---|
| `ARGOS_JOBS` | `argos.jobs.document.extract.v1` | outbox dispatcher | `document-extractor-v1` |
| `ARGOS_JOBS` | `argos.jobs.source.ingest.v1` | outbox dispatcher | `source-ingestor-v1` |
| `ARGOS_JOBS` | `argos.jobs.case.analyze.v1` (lo crean el gateway en `analyze_notice` y el resumer al cerrar el último documento) | outbox dispatcher | `case-analyzer-v1` |
| `ARGOS_EVENTS` | `argos.events.document.extracted.v1` | outbox de extracción | `workflow-resumer-v1` |
| `ARGOS_EVENTS` | `argos.events.document.failed.v1` | outbox de extracción | `workflow-resumer-v1` |
| `ARGOS_EVENTS` | `argos.events.case.completed.v1` | outbox del workflow | consumidores autorizados |

El payload canónico de trabajos y eventos es:

```json
{"job_id":"job:01...","attempt":1}
```

No contiene `tenant_id`, URLs, texto, resultado ni error interno. Esos datos se
leen de SurrealDB tras autenticar al workload. Las cabeceras pueden propagar
`Nats-Msg-Id` y `traceparent`; tampoco contienen datos de negocio.

### Publicación y consumo

1. El caso de uso crea trabajo y outbox en una transacción.
2. El dispatcher reclama una entrada no publicada con lease.
3. Publica con `Nats-Msg-Id = {job_id}:{attempt}`.
4. Tras confirmación de JetStream marca el outbox como publicado.
5. El consumidor recibe, relee el trabajo y reclama el intento mediante una
   transición condicional.
6. Si el trabajo ya terminó o el intento es antiguo, confirma sin repetir la
   operación.
7. Tras guardar objetos, el worker confirma extracción, estado y evento de
   outbox en una misma transacción y después hace ACK del comando.
8. El dispatcher publica el evento pendiente y el resumer lo consume. Si
   cualquier proceso falla antes de confirmar, la entrega se repite o el outbox
   se recupera; la reclamación e idempotencia impiden duplicar el resultado.

La entrega es al menos una vez. No se promete exactamente una vez; se obtiene
el efecto equivalente mediante estado durable e idempotencia.

### Intentos y arrendamientos

- Cada intento es una entrada de outbox propia con `{job_id, attempt}` y un
  `not_before` que aplica el backoff; el dispatcher no publica antes de esa
  hora.
- Al reclamar, el consumidor fija `lease_until` en el trabajo y lo renueva
  mientras trabaja.
- Un fallo transitorio cierra el intento como `failed` y, en la misma
  transacción, crea el intento siguiente y su comando si queda presupuesto; si
  no, el trabajo pasa a `failed`.
- El dispatcher recupera arrendamientos vencidos: un trabajo `running` cuyo
  `lease_until` ya pasó cierra su intento como `lost` y se reencola igual, sin
  tocar resultados. Si el intento perdido ya había confirmado su cierre, la
  transición condicional lo detecta y no hace nada.
- La reentrega propia de JetStream (sin ACK a tiempo) es solo red de seguridad:
  una entrega cuyo `attempt` no es el actual se confirma sin efecto. La ventana
  de deduplicación de JetStream es mayor que el arrendamiento del dispatcher.

## 9. Pipeline de documentos

### Ingreso

1. El gateway valida credencial, tenant, extensión, tipo declarado, firma real
   y tamaño. El caso destino existe sin veredicto o se crea nuevo; un caso con
   veredicto recibe un caso vinculado (R12).
2. Crea una referencia de artefacto `uploading` y calcula SHA-256 mientras
   transmite el original al almacén privado, sin cargarlo completo en memoria.
3. En una transacción busca en el mismo caso un documento con el mismo hash.
   Nunca consulta otros casos ni otros tenants.
4. Si existe, responde con el documento y el trabajo existentes y deja el
   objeto recién subido para el janitor. Si no, marca el artefacto `available`,
   registra documento, hash, MIME, tamaño y caducidad, crea trabajo y comando
   de outbox y pone el caso en `awaiting_processing`, todo en esa transacción.
5. Tras confirmar, el gateway responde aceptación sin esperar al worker; el
   dispatcher publica el comando de forma independiente y recuperable.

Una subida interrumpida queda `uploading`, nunca se encola y un recolector borra
registro y objeto exactos tras su TTL. Un fallo al confirmar intenta borrar el
objeto y queda igualmente cubierto por ese recolector.

### Extracción

1. El worker reclama el trabajo y obtiene acceso breve al objeto exacto.
2. Verifica hash, tipo y tamaño de nuevo; una discrepancia es fallo permanente.
   Completa la validación profunda de R19: cifrado, corrupción, contenido
   activo no admitido y exceso de páginas son fallos permanentes con código
   estable que dejan el documento `rejected`.
3. Extrae texto y metadatos por página. Solo aplica OCR a páginas sin texto útil.
4. Normaliza saltos y orden, pero conserva relación página/posición.
5. Genera texto completo comprimido, manifiesto y chunks deterministas.
6. Sube derivados a RustFS, verifica que son legibles y registra sus hashes.
7. Cierra intento y extracción y crea el evento de outbox en una transacción;
   el dispatcher lo publica después.

### Reanudación

El resumer recibe la referencia, relee el trabajo y el caso y comprueba tenant y
estado. Si el caso no tiene más documentos pendientes, crea el trabajo
`case.analyze` y su comando en una transacción; si los tiene, confirma y espera
al siguiente evento. El case analyzer reclama ese intento, pasa el caso de
`awaiting_processing` a `analyzing`, obtiene solo chunks autorizados y aplica
W1 dentro de una ejecución correlacionada con el caso, exista o no la sesión
original. Si el proceso muere, el arrendamiento vence y el trabajo se
reentrega. Si la extracción falló de forma terminal y el caso tiene otra
entrada analizable, el análisis arranca igual y el veredicto es `partial`; sin
otra entrada, el caso termina `failed`. La salida sigue disponible por
`get_case` aunque no exista cliente conectado.

## 10. Artefactos en RustFS

El puerto de aplicación se llama `S3ObjectStore` y solo ofrece operaciones
necesarias: escritura streaming con hash, lectura acotada, comprobación de
metadatos, URL firmada breve y borrado exacto condicionado por referencia.

Claves conceptuales, siempre dentro del tenant y del caso:

```text
tenants/{tenant_id}/cases/{case_id}/documents/{document_id}/source.pdf
tenants/{tenant_id}/cases/{case_id}/extractions/{extraction_id}/text.txt.zst
tenants/{tenant_id}/cases/{case_id}/extractions/{extraction_id}/manifest.json
tenants/{tenant_id}/cases/{case_id}/extractions/{extraction_id}/pages/{page}.png
```

Las claves no son autorización ni se exponen al agente. El registro operacional
guarda bucket, clave, versión, hash, tamaño, MIME, creación y caducidad. Los
buckets son privados, con TLS y credenciales separadas. No se depende de Object
Lock como única defensa: retención, backups y borrado seguro tienen controles
independientes.

RustFS es la elección de despliegue para Argos. La neutralidad del puerto evita
acoplar el dominio a una implementación S3 concreta y permite sustituirla sin
cambiar agentes, workflows ni trabajos.

## 11. Reintentos, idempotencia y fallos

- Clave de extracción: tenant + SHA-256 + versión de extractor + opciones
  normalizadas.
- Cada entrega declara el intento que espera procesar; un intento menor que el
  actual es obsoleto.
- El ACK ocurre después de persistir el resultado y su evento de outbox, o de
  reconocer que ambos ya existían.
- Los fallos se clasifican como transitorios o permanentes. Red, dependencia no
  disponible y lease perdido son transitorios; PDF corrupto, cifrado o hash
  inconsistente son permanentes.
- Los reintentos usan backoff y un máximo configurable. Agotado, el estado
  `failed` de SurrealDB actúa como DLQ operable.
- Reprocesar es un comando del curador: crea un trabajo nuevo vinculado al
  anterior y, si corresponde, una nueva versión de extracción; devuelve un caso
  `failed` o `partial` a `awaiting_processing` y conserva el veredicto previo
  como versión superada. No se resetea ni borra historia.
- Un evento duplicado, tardío o fuera de orden no puede retroceder el estado del
  caso ni reemplazar una extracción más reciente. Esa transición hacia atrás
  solo existe como comando del curador.

## 12. Privacidad, retención y borrado

- El PDF, texto completo, chunks y capturas de página caducan a los 30 días por
  defecto. El caso, señales y citas mínimas duran 12 meses.
- El proceso de retención marca primero los registros caducados, comprueba que no
  existen referencias vivas y borra objetos exactos; después deja evidencia de
  la operación sin conservar contenido.
- Ni payloads NATS, sesiones Agno, logs ni trazas contienen el documento o texto
  completo. La sesión no persiste los mensajes de herramienta que transportan
  chunks; las observaciones de modelo en Langfuse llevan modelo, tokens, coste,
  duración e identificadores, con entradas y salidas ocultas por la
  instrumentación. La imagen del aviso sigue la misma regla.
- Los mensajes de error públicos no incluyen claves de objeto, SQL, stack traces
  ni texto del documento.
- Un borrado de tenant invalida accesos y elimina sus datos siguiendo el mismo
  recorrido referencial; nunca mediante un prefijo construido con entrada no
  validada.

## 13. Observabilidad

Una correlación une petición de gateway, ejecución AgentOS, caso, trabajo,
intento, publicaciones NATS y worker. Las trazas incluyen identificadores
técnicos, estados, duración, versión y tamaños, pero no contenido sensible.

Métricas mínimas:

- trabajos en cola, en ejecución, completados y fallidos por tipo;
- antigüedad del trabajo más viejo y tiempo por estado;
- reintentos, entregas duplicadas y eventos obsoletos;
- duración, páginas, bytes y uso de OCR por extracción;
- outbox pendiente y tiempo hasta publicación;
- casos esperando documento y tiempo hasta veredicto;
- errores MCP por capacidad y denegaciones de autorización.

No hay fallo silencioso: toda excepción termina trazada y en una transición o
reintento visible.

## 14. Desarrollo y despliegue

El compose de S02 añadirá NATS JetStream, RustFS, el worker (Python, en este
repositorio), dispatcher, resumer, case analyzer y janitor a la plataforma S01.
Todos los puertos publicados al host seguirán en loopback.
Redis no será dependencia de código de Argos. Las credenciales se documentan en
`.env.example` con valores locales y se inyectan por secretos en despliegue.

La implantación se divide sin cambiar estas fronteras:

1. esquema de trabajos, intentos, artefactos, extracciones, chunks y outbox;
2. puertos y fakes tipados, sin `Any`, validados por mypy y pyright estrictos;
3. NATS, dispatcher (outbox y arrendamientos) y consumidores idempotentes;
4. RustFS y `S3ObjectStore` con streaming;
5. worker de PDF y OCR;
6. AgentOS, agentes, Team, Workflow y herramientas MCP acotadas;
7. gateway API/A2A, reanudación y pruebas de reinicio;
8. observabilidad con enmascarado, janitor (staging y retención) y
   endurecimiento de credenciales.

## 15. Criterios de aceptación que se convertirán en casos S02.n

- AgentOS publica solo las capacidades del gateway y no descubre especialistas
  ni workers.
- Cada agente solo puede invocar sus herramientas MCP autorizadas y no puede
  cruzar tenant ni entrar en `agno/sessions`.
- Enviar un PDF válido responde antes de extraer y deja documento, trabajo y
  outbox en estado consistente.
- Un fallo entre la transacción y la publicación no pierde el trabajo; el
  dispatcher lo entrega al recuperarse.
- Un fallo después de cerrar una extracción y antes de publicar su evento no
  impide reanudar el caso; el evento queda recuperable en el outbox.
- Una subida interrumpida no crea un trabajo procesable y su artefacto se limpia
  al expirar el TTL de staging.
- El payload NATS contiene solo `job_id` y `attempt` y todos los consumidores son
  durables con ACK explícito.
- Dos entregas del mismo intento producen una sola extracción observable.
- Reiniciar AgentOS, dispatcher, resumer o worker en cada frontera crítica no
  pierde ni duplica el resultado.
- Un documento de otro tenant nunca se puede consultar por API, A2A, MCP, worker
  ni URL firmada.
- Los agentes no tienen credenciales de RustFS y las sesiones no contienen texto
  completo.
- PDF corrupto o cifrado termina con código estable y sin reintentos inútiles;
  un fallo transitorio reintenta y queda auditable.
- El evento de finalización contiene referencias; el workflow relee SurrealDB,
  reanuda el caso y emite el veredicto aunque la sesión original haya caducado.
- Reprocesar conserva la extracción anterior y crea una versión nueva.
- La retención elimina objetos y chunks caducados sin dañar casos vigentes.
- RustFS supera una prueba de escritura, verificación de hash, lectura, borrado y
  restauración antes de usarse en producción; Argos no depende de Object Lock
  como única protección.
- Las trazas correlacionan toda la cadena sin incluir contenido sensible.
- Enviar el mismo PDF al mismo caso devuelve el documento y el trabajo
  existentes; enviarlo a otro caso crea otro documento y otra extracción.
- Un worker que muere con un intento abierto: al vencer el arrendamiento el
  trabajo vuelve a `queued` con un intento nuevo, o a `failed` si agotó el
  presupuesto, y ningún resultado a medias cuenta.
- `analyze_notice` deja caso y trabajo `case.analyze` en una transacción; si el
  proceso se reinicia a mitad del análisis, el caso termina igualmente y
  `get_case` lo devuelve.
- Enviar un documento a un caso con veredicto crea un caso vinculado y no
  modifica el veredicto.
- Reprocesar un caso `failed` lo devuelve a `awaiting_processing` y conserva el
  veredicto anterior como versión superada.
- Tras un análisis con chunks, `agno/sessions` no contiene el texto sintético
  del fixture y ninguna observación de Langfuse lo contiene.
- `find_entity_history` desde el tenant B sobre una entidad vista solo por el
  tenant A devuelve agregados y ningún identificador de caso, cita ni tenant.
- Un veredicto `partial` sin señales lleva nivel `undetermined` y acciones.
- El agente de documentos no dispone de herramienta para crear ni reprocesar
  trabajos.

## 16. Casos anclados

Los casos siguen el orden de implantación del §14. Cada uno cita los flujos y
reglas de la funcional que cubre; `spec-check` exige un test por caso.

## S02.1 El esquema del libro de trabajos se aplica de forma idempotente

- Dado una SurrealDB con la base S01 aplicada
- Cuando `bootstrap-db` se ejecuta dos veces seguidas
- Entonces `argos/ops` contiene las tablas `tenant`, `case`, `artifact`,
  `document`, `job`, `attempt`, `outbox_entry`, `extraction` y `chunk`, todas
  `SCHEMAFULL`, el usuario `ledger` que usan gateway, dispatcher y worker en
  desarrollo, y `schema_version:current` tiene la versión que declara
  `bootstrap-db` (constitución §7, §14; R21, R22)

## S02.2 Enviar un PDF válido responde antes de extraer y deja documento, trabajo y outbox consistentes

- Dado un tenant activo y un PDF sintético válido
- Cuando el cliente lo envía sin indicar caso
- Entonces la respuesta llega sin esperar al worker y contiene `case_id`,
  `document_id` y `job_id`; el caso está en `awaiting_processing`; el documento
  está `accepted` con el SHA-256 y el tamaño del fichero; su artefacto está
  `available` bajo la clave `tenants/{t}/cases/{c}/documents/{d}/source.pdf` y
  el objeto existe en el almacén; el trabajo `document.extract` está `queued`
  en el intento 1 con el máximo de intentos de la política; existe una sola
  entrada de outbox, comando `argos.jobs.document.extract.v1` con
  `message_id = {job_id}:1`, y nada se ha publicado todavía en el bus (W5.1–3,
  R20, R23; constitución §7)

## S02.3 El mismo PDF en el mismo caso devuelve lo existente; en otro caso es otro documento

- Dado un caso con un documento aceptado
- Cuando se envía el mismo PDF a ese caso y después sin caso
- Entonces el primer envío devuelve el documento y el trabajo existentes marcados
  como reutilizados y el caso sigue con un solo trabajo; el segundo crea otro
  caso, otro documento y otro trabajo (R22)

## S02.4 Un PDF que no supera la validación barata se rechaza antes de encolar

- Dado un fichero que no empieza por `%PDF-`, uno con extensión distinta de
  `.pdf`, uno con tipo declarado distinto de `application/pdf` o uno cuyo
  tamaño declarado supera el límite
- Cuando se envía
- Entonces se rechaza con `document.not_pdf`, `document.bad_extension`,
  `document.bad_mime` o `document.too_large` respectivamente y no queda ningún
  objeto en el almacén ni ningún trabajo (R19, R28)

## S02.5 Un fallo entre la transacción y la publicación no pierde el trabajo

- Dado un comando pendiente en el outbox y un bus que rechaza la primera
  publicación
- Cuando el dispatcher ejecuta dos pasadas
- Entonces tras la primera el comando sigue `pending` sin arrendamiento y el bus
  no tiene nada; tras la segunda está `published` y el bus contiene un solo
  mensaje con el subject del trabajo, cabecera `Nats-Msg-Id = {job_id}:1` y un
  payload que contiene exactamente `job_id` y `attempt`; una tercera pasada no
  publica nada (R25; constitución §9; S02 §8)

## S02.6 Dos entregas del mismo intento producen una sola reclamación efectiva

- Dado un trabajo `queued` en el intento 1
- Cuando dos consumidores reclaman el intento 1 y un tercero reclama el 2
- Entonces solo el primero obtiene el intento; el trabajo está `running` con
  `lease_until = ahora + arrendamiento`; existe un único intento, del primer
  consumidor; los otros dos deben confirmar sin efecto (R22, R25; S02 §8)

## S02.7 Un intento cuyo arrendamiento vence se reencola con intento nuevo o termina failed

- Dado un trabajo reclamado con máximo de dos intentos
- Cuando vence el arrendamiento y el dispatcher recupera arrendamientos, y se
  repite con el segundo intento
- Entonces la primera vez el intento 1 queda `lost`, el trabajo vuelve a
  `queued` en el intento 2 sin arrendamiento y existe el comando del intento 2
  con `not_before = ahora + backoff`, que el dispatcher no ve hasta esa hora;
  la segunda vez el trabajo termina `failed` con error público
  `job.attempts_exhausted`, hay un evento `argos.events.document.failed.v1`
  pendiente y no existe comando para un intento 3 (R21, R28; S02 §8)

## S02.8 Un fallo transitorio reintenta con backoff y uno permanente no

- Dado un trabajo reclamado
- Cuando el consumidor lo cierra con un fallo transitorio, reclama el intento 2
  y lo cierra con `pdf.encrypted`, y después repite ese cierre
- Entonces el fallo transitorio deja el trabajo `queued` en el intento 2 con su
  comando retrasado por backoff; el permanente deja el trabajo `failed` con
  error público `pdf.encrypted`, el documento `rejected`, ambos intentos
  `failed` con su tipo de error, un solo evento de fallo y ningún intento 3; el
  cierre repetido se reconoce como obsoleto sin efecto (R19, R21, R28; S02 §11)

## S02.9 El cierre de una extracción y su evento nacen en una transacción

- Dado un trabajo reclamado
- Cuando el worker confirma la extracción con sus artefactos y chunks, y
  después vuelve a confirmarla
- Entonces el trabajo está `completed` sin arrendamiento, el intento
  `succeeded`, el documento conoce sus páginas, existe una extracción
  `available` con sus chunks ordenados y un evento
  `argos.events.document.extracted.v1` pendiente; la segunda confirmación se
  reconoce como obsoleta y no añade extracción ni evento (W5.5, R22, R25; S02
  §8.7)

## S02.10 Un documento de otro tenant nunca se puede consultar

- Dado un documento aceptado por un tenant
- Cuando otro tenant consulta su trabajo, su caso o su documento
- Entonces los tres responden como inexistentes; el propio tenant ve el estado
  público del trabajo y nunca el error interno (R16, R28)

## S02.11 analyze_notice deja caso y trabajo case.analyze en una transacción

- Dado un tenant activo y un aviso breve válido
- Cuando se abre el caso, se repite el mismo aviso con distinto espaciado y
  mayúsculas, lo envía otro tenant y se envían un aviso demasiado largo y uno
  vacío
- Entonces la primera apertura deja el caso `received` con su hash, un trabajo
  `case.analyze` `queued` y su comando `argos.jobs.case.analyze.v1` pendiente;
  la repetición devuelve el mismo caso y trabajo sin crear nada; el otro tenant
  obtiene un caso distinto; los dos últimos se rechazan con
  `notice.text_too_long` y `notice.empty` (W1.2–3, R1, R9, R12)

## S02.12 Los streams y los consumidores durables se declaran de forma idempotente

- Dado un NATS con JetStream recién arrancado
- Cuando `bootstrap-bus` se ejecuta dos veces seguidas
- Entonces existen `ARGOS_JOBS` con los tres subjects de comando y retención de
  cola de trabajo, y `ARGOS_EVENTS` con los tres de evento y retención por
  límites; la ventana de deduplicación de ambos supera el arrendamiento del
  dispatcher; y los consumidores `document-extractor-v1`, `case-analyzer-v1`,
  `source-ingestor-v1` y `workflow-resumer-v1` son durables, con ack explícito,
  espera de confirmación igual al arrendamiento del trabajo, su máximo de
  entregas y exactamente los subjects de su cometido (constitución §9; S02 §8)

## S02.13 El comando confirmado llega al consumidor durable con solo job_id y attempt

- Dado un documento aceptado cuyo comando sigue pendiente en el outbox
- Cuando el dispatcher publica y el consumidor `document-extractor-v1` recoge su
  entrega
- Entonces recibe un solo mensaje en el subject del trabajo, con cuerpo
  `{job_id, attempt}` y primera entrega; reclama el intento, confirma, la
  entrada del outbox queda `published` y no hay nada más que recoger (R24, R25;
  constitución §9)

## S02.14 Publicar dos veces el mismo intento entrega una sola vez

- Dado un comando ya publicado cuya entrada de outbox se vuelve a publicar,
  como haría un dispatcher que murió antes de marcarla
- Cuando el consumidor recoge sus entregas
- Entonces recibe un único mensaje: la deduplicación por `Nats-Msg-Id` descarta
  la repetición dentro de la ventana (R22; S02 §8)

## S02.15 Una entrega que el consumidor no confirma vuelve a entregarse y la segunda no tiene efecto

- Dado un consumidor que reclama el intento y no confirma su entrega
- Cuando la entrega se repite y otro consumidor intenta reclamar el mismo
  intento
- Entonces la segunda entrega llega con contador 2 y el mismo cuerpo, el
  segundo consumidor la reconoce como obsoleta y la confirma sin efecto, sigue
  existiendo un único intento del primer consumidor y el outbox no gana
  entradas (R21, R25; S02 §8)

## S02.16 El bucle del dispatcher publica lo pendiente y reencola arrendamientos vencidos

- Dado un trabajo reclamado cuyo arrendamiento vence mientras el bucle duerme
- Cuando el dispatcher se ejecuta hasta que se le pide parar
- Entonces la primera pasada publica el comando del intento 1, la segunda no
  publica nada y reencola el trabajo con el intento 2, la tercera publica el
  comando del intento 2 pasado su backoff, el bus recibió exactamente esos dos
  mensajes y el bucle termina cuando su condición de parada lo pide (R21, R25,
  R28)

## S02.17 El bucket de artefactos se crea de forma idempotente y no sirve nada sin firma

- Dado un RustFS recién arrancado
- Cuando `bootstrap-store` se ejecuta dos veces y se escribe un objeto
- Entonces el bucket existe, una petición sin firmar al bucket y otra al objeto
  reciben 403, y el objeto sí se lee con credenciales (constitución §10; R8)

## S02.18 Un objeto se escribe en flujo con su hash y se relee de forma acotada

- Dado un contenido que llega en varios trozos
- Cuando se escribe declarando su tamaño y su tipo y después se consulta
- Entonces la escritura devuelve el SHA-256 del contenido completo y su tamaño
  sin haberlo cargado entero en memoria; los metadatos devuelven tamaño y tipo;
  la lectura acotada devuelve el contenido y falla si el límite es menor que el
  objeto; una clave inexistente no tiene metadatos ni contenido (S02 §10)

## S02.19 Un tamaño declarado que no coincide con lo subido no deja objeto utilizable

- Dado un cuerpo más corto que el tamaño declarado
- Cuando se intenta escribir
- Entonces la escritura falla por discrepancia de tamaño y la clave sigue sin
  objeto (W5.1, R19; S02 §9)

## S02.20 Una URL firmada breve sirve solo su objeto y caduca

- Dado un objeto escrito y otro distinto
- Cuando se firma una URL de lectura para el primero, se reutiliza la firma del
  segundo sobre la clave del primero, se firma una ya caducada y se pide sin
  firma
- Entonces solo la primera devuelve el contenido; las otras tres reciben 403
  (constitución §10; R16)

## S02.21 El borrado exacto elimina su objeto y deja intactos los demás

- Dado dos objetos del almacén
- Cuando se borra uno y se repite el borrado
- Entonces ese objeto desaparece, el otro sigue disponible y borrar lo que ya
  no está no falla (R18; S02 §12)

## S02.22 El ingreso completo deja el original en el almacén real

- Dado un tenant activo y un PDF sintético
- Cuando se envía con el almacén RustFS en lugar del doble en memoria
- Entonces el artefacto registrado apunta a
  `tenants/{t}/cases/{c}/documents/{d}/source.pdf`, el objeto existe con el
  tamaño y el tipo del original y su contenido es byte a byte el enviado (W5.2;
  R23; S02 §10)

## S02.23 El worker extrae el texto embebido, sube los derivados y cierra la extracción

- Dado un PDF con texto y su trabajo reclamado
- Cuando el worker lo extrae
- Entonces el trabajo queda `completed`; existe una extracción `available` con
  una página, cero páginas con OCR y la versión de extractor del trabajo; sus
  chunks conservan página y orden; el texto completo comprimido y el manifiesto
  están en el almacén bajo las claves de esa extracción; el manifiesto declara
  el origen y el tamaño de cada página y las posiciones de los chunks; queda un
  evento `argos.events.document.extracted.v1` pendiente y el OCR no se invocó
  (W5.4-5; R22, R23)

## S02.24 Solo se aplica OCR a las páginas sin texto utilizable

- Dado un PDF de dos páginas donde la primera lleva texto y la segunda es una
  imagen escaneada
- Cuando el worker lo extrae
- Entonces el OCR se invoca exactamente una vez, la extracción declara dos
  páginas y una con OCR, el chunk de la primera página viene del texto
  incrustado y el de la segunda del reconocimiento, y el documento registra sus
  dos páginas (W5.4; constitución §10)

## S02.25 Un documento que el worker no puede leer termina en fallo permanente

- Dado un PDF corrupto, uno cifrado, uno con más páginas de las admitidas o uno
  cuyo objeto ya no coincide con el hash registrado
- Cuando el worker intenta extraerlo
- Entonces el trabajo termina `failed` con código público `pdf.damaged`,
  `pdf.encrypted`, `pdf.too_many_pages` o `document.hash_mismatch`, el documento
  queda `rejected`, no se crea extracción, el intento queda cerrado como fallo
  permanente y hay un evento `argos.events.document.failed.v1` (R19, R21, R28;
  S02 §11)

## S02.26 El bucle del worker extrae lo que reclama y confirma lo que ya no le toca

- Dado dos comandos entregados, uno de un trabajo que otro consumidor ya
  reclamó
- Cuando el worker ejecuta una pasada
- Entonces extrae y cierra el suyo, confirma el otro sin trabajar, ninguna
  entrega queda sin confirmar y el trabajo ajeno sigue como estaba (R21, R25;
  S02 §8)
