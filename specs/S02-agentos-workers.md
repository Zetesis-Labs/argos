# S02 · AgentOS, clúster de agentes y workers

**Estado**: arquitectura aprobada; implementación y casos de aceptación
pendientes.

Esta vertical convierte la base S01 en un AgentOS capaz de coordinar agentes
especialistas y trabajos asíncronos. Cubre W1, W2 y W5; R8, R12, R15–R28; y la
constitución §3–§4, §6–§12.

La numeración `S02.n` se asignará junto con los tests que fallen al iniciar la
implementación. Hasta entonces, este documento fija decisiones y criterios de
aceptación, pero `spec-check` no lo presenta como código terminado.

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
| **Outbox dispatcher** | Publicar comandos y eventos confirmados y marcar su entrega | Crear o cerrar trabajos de negocio por sí mismo |
| **NATS JetStream** | Entregar comandos y eventos referenciados | Ser la única copia del estado o transportar documentos |
| **Document worker** | Reclamar, verificar, extraer, persistir y anunciar | Conversar, puntuar o conservar estado local durable |
| **RustFS** | Guardar originales y derivados privados | Resolver autorización de negocio sin SurrealDB |
| **Workflow resumer** | Consumir eventos, releer estado y reanudar el caso | Confiar en el contenido del evento como fuente de verdad |

## 4. Catálogo inicial de agentes

| Agente | Entrada | Herramientas | Salida |
|---|---|---|---|
| `triage_agent` | Aviso o chunks autorizados | normalización y clasificación | identificadores, idioma y tipologías candidatas |
| `registries_agent` | Entidades normalizadas | consulta de advertencias | señales de registros oficiales |
| `domain_agent` | Dominios | RDAP, certificados y reputación | señales técnicas fechadas |
| `patterns_agent` | Texto autorizado | catálogo de patrones | señales con cita y posición |
| `memory_agent` | Identificadores y tenant | consultas MCP de reincidencia | apariciones y revisiones previas |
| `document_agent` | Caso y documento | crear/consultar/reprocesar trabajo | referencias y estado; nunca texto completo |
| `verdict_writer` | nivel calculado y señales | guía de acciones | explicación; no puede modificar el nivel |
| `conversation_agent` | pregunta, caso y sesión | lectura de veredicto y memoria autorizada | respuesta sin mutar señales ni nivel |

`investigation_team` agrupa triaje, registros, dominio, patrones y memoria.
`verdict_workflow` es el único coordinador autorizado para mover el caso entre
estados y emitir un veredicto.

## 5. Frontera API y A2A

El gateway publica capacidades, no la topología interna:

| Capacidad | Tipo | Respuesta |
|---|---|---|
| `analyze_notice` | síncrona con límite | caso y veredicto o estado parcial |
| `submit_document` | asíncrona | caso, documento, trabajo y `queued` |
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

## 6. Modelo operacional en SurrealDB

La jerarquía obligatoria es:

```text
tenant → case → document → extraction → chunk
                  └──────→ job → attempt
case → entity → signal/evidence
transaction → outbox_entry
```

Reglas del modelo:

- toda fila de negocio lleva tenant y las consultas lo filtran antes de devolver
  contenido;
- todo documento pertenece al menos a un caso; una extracción pertenece a una
  versión concreta del documento;
- el trabajo guarda tipo, versión, opciones normalizadas, estado, intento actual,
  presupuesto, error público, error interno y correlación de traza;
- cada intento conserva tiempos, consumidor y resultado para auditar entregas
  repetidas;
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
  `find_registry_matches`, `find_entity_history`, `submit_document_job`,
  `get_extraction_chunks`); no reciben la herramienta de consulta general como
  interfaz normal de producto.
- El MCP valida identidad, tenant, capacidad, caso y campos de salida.
- El worker puede usar un adaptador directo tipado para transacciones y leases,
  con permisos sobre trabajos, intentos, documentos y extracciones; no puede
  leer sesiones ni revisar casos.
- Los agentes no reciben claves de RustFS. Gateway y worker usan credenciales
  distintas o URLs firmadas breves con operación, objeto y caducidad acotados.
- El usuario genérico `agent` de S01 es una facilidad de desarrollo y debe
  dividirse antes de un despliegue productivo de S02.

## 8. Contrato NATS JetStream

### Streams y subjects

| Stream | Subjects iniciales | Productor | Consumidor durable |
|---|---|---|---|
| `ARGOS_JOBS` | `argos.jobs.document.extract.v1` | outbox dispatcher | `document-extractor-v1` |
| `ARGOS_JOBS` | `argos.jobs.source.ingest.v1` | outbox dispatcher | `source-ingestor-v1` |
| `ARGOS_JOBS` | `argos.jobs.case.analyze.v1` | outbox dispatcher | `case-analyzer-v1` |
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

## 9. Pipeline de documentos

### Ingreso

1. El gateway valida credencial, tenant, PDF, tamaño, páginas y firma del archivo.
2. Crea una referencia de artefacto `uploading` y calcula SHA-256 mientras
   transmite el original al almacén privado, sin cargarlo completo en memoria.
3. En una transacción marca el artefacto `available`, registra documento, hash,
   MIME, tamaño y caducidad, y busca una extracción reutilizable con el mismo
   tenant, hash, versión y opciones. Nunca consulta coincidencias de otro tenant.
4. Si existe una extracción, la vincula al caso sin encolar. Si no, crea trabajo
   y comando de outbox dentro de esa misma transacción.
5. Tras confirmar, el gateway responde aceptación sin esperar al worker; el
   dispatcher publica el comando de forma independiente y recuperable.

Una subida interrumpida queda `uploading`, nunca se encola y un recolector borra
registro y objeto exactos tras su TTL. Un fallo al confirmar intenta borrar el
objeto y queda igualmente cubierto por ese recolector.

### Extracción

1. El worker reclama el trabajo y obtiene acceso breve al objeto exacto.
2. Verifica hash, tipo y tamaño de nuevo; una discrepancia es fallo permanente.
3. Extrae texto y metadatos por página. Solo aplica OCR a páginas sin texto útil.
4. Normaliza saltos y orden, pero conserva relación página/posición.
5. Genera texto completo comprimido, manifiesto y chunks deterministas.
6. Sube derivados a RustFS, verifica que son legibles y registra sus hashes.
7. Cierra intento y extracción y crea el evento de outbox en una transacción;
   el dispatcher lo publica después.

### Reanudación

El resumer recibe la referencia, relee el trabajo y el caso, comprueba tenant y
estado y entrega al workflow solo chunks autorizados. Si la sesión original ya
no existe, crea una ejecución de reanudación correlacionada con el caso. El
workflow pasa de `awaiting_processing` a `analyzing` y aplica W1. La salida sigue
disponible por `get_case` aunque no exista cliente conectado.

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
- Reprocesar crea un trabajo nuevo vinculado al anterior y, si corresponde, una
  nueva versión de extracción. No se resetea ni borra historia.
- Un evento duplicado, tardío o fuera de orden no puede retroceder el estado del
  caso ni reemplazar una extracción más reciente.

## 12. Privacidad, retención y borrado

- El PDF, texto completo, chunks y capturas de página caducan a los 30 días por
  defecto. El caso, señales y citas mínimas duran 12 meses.
- El proceso de retención marca primero los registros caducados, comprueba que no
  existen referencias vivas y borra objetos exactos; después deja evidencia de
  la operación sin conservar contenido.
- Ni payloads NATS, sesiones Agno, logs ni trazas contienen el documento o texto
  completo.
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

El compose de S02 añadirá NATS JetStream, RustFS, el worker, dispatcher y resumer
a la plataforma S01. Todos los puertos publicados al host seguirán en loopback.
Redis no será dependencia de código de Argos. Las credenciales se documentan en
`.env.example` con valores locales y se inyectan por secretos en despliegue.

La implantación se divide sin cambiar estas fronteras:

1. esquema de trabajos, intentos, artefactos, extracciones, chunks y outbox;
2. puertos y fakes tipados, sin `Any`, validados por mypy y pyright estrictos;
3. NATS, dispatcher y consumidores idempotentes;
4. RustFS y `S3ObjectStore` con streaming;
5. worker de PDF y OCR;
6. AgentOS, agentes, Team, Workflow y herramientas MCP acotadas;
7. gateway API/A2A, reanudación y pruebas de reinicio;
8. observabilidad, retención y endurecimiento de credenciales.

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
