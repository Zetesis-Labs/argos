# Constitución de Argos

Principios que toda especificación, plan y cambio de código de este repositorio
deben respetar. Si una spec o un cambio los contradice, se cambia la spec o el
cambio, no la constitución; y si hay que cambiar la constitución, se hace en un
commit propio que lo diga.

Argos analiza avisos y documentos relacionados con posibles fraudes financieros
y devuelve un veredicto explicado con evidencias. Se ejecuta como un sistema de
agentes especializados con memoria operacional compartida y procesamiento
asíncrono para los trabajos pesados.

## 1. Idioma

- **El código es en inglés**: identificadores, ficheros, rutas de la API, códigos
  de error y mensajes de commit. Los nombres propios del dominio (`CNMV`, `FCA`,
  `I-SCAN`, `chiringuito` como tipología) se conservan tal cual.
- **La documentación es en español**: specs, README, constitución, docstrings de
  los tests, comentarios cuando existen y los textos que ve el consultante.

## 2. Desarrollo dirigido por especificación, con anclaje

- La especificación **funcional** vive en
  `specs/argos/{iniciativa}/functional-specs.md` y describe el qué: actores,
  flujos (`W1`, `W2`…), reglas (`R1`, `R2`…) y conceptos.
- Cada vertical técnico tiene una spec en `specs/Sxx-*.md` con casos `Sxx.n` en
  forma `Dado / Cuando / Entonces`. Cada caso cita qué flujos o reglas de la
  funcional cubre.
- **La suite se escribe antes que la implementación.** Un caso nuevo entra primero
  como test que falla, después como código que lo pone en verde.
- Cada test vertical cita el identificador del caso en la primera línea de su
  docstring; `uv run spec-check` falla si un caso `Sxx.n` no tiene test o un
  test cita un caso inexistente.
- Las specs son el pliego. Se cambian antes que el código, y se cambian cuando el
  código descubre que estaban mal, con el porqué en el commit.

## 3. Núcleo funcional, cáscara imperativa

- `argos/core` no hace I/O: ni red, ni base de datos, ni reloj, ni aleatoriedad.
  Ahí viven la extracción y normalización de identificadores, la clasificación
  de señales, la puntuación, la fusión de evidencias y la composición del
  veredicto. Todo son funciones puras con test unitario propio.
- Toda dependencia externa entra por un puerto (`typing.Protocol`) declarado en
  `argos/core/ports.py` y se implementa en adaptadores. Cada puerto tiene al
  menos un adaptador real y un `fake` para tests.
- Los casos de uso reciben sus dependencias como argumentos, nunca por herencia
  ni por variables globales. `Clock` es un puerto que se inyecta.
- `argos/agents` declara agentes, equipos y workflows de Agno y los cablea con
  herramientas. Ninguna regla de negocio vive en un prompt.
- Los workers ejecutan transformaciones pesadas y deterministas. No toman
  decisiones de riesgo, no conversan y no se convierten en agentes A2A.

## 4. El LLM no puntúa ni gobierna el proceso

- El nivel de riesgo lo decide `core.score` a partir de señales tipadas. El LLM
  extrae, interpreta la salida de las herramientas y redacta; no asigna niveles.
- Validaciones, permisos, transiciones de estado, reintentos, presupuestos de
  tiempo, deduplicación y retención son código determinista fuera de los prompts.
- Toda señal lleva fuente, fecha de observación, valor y peso. Sin evidencia no
  hay señal, y una señal sin fecha no existe.
- Las herramientas son deterministas: misma entrada y misma fecha, misma salida.
  Lo que consulta fuera se cachea por día.

## 5. Lenguaje del veredicto

- Argos habla de indicios y coincidencias. Nunca afirma «es una estafa», nunca
  imputa un delito y nunca señala a una persona física como estafadora.
- Todo veredicto incluye qué hacer ahora y dónde acudir. Sin acciones no hay
  veredicto.
- La degradación es explícita: si una fuente o un trabajo no respondió, el
  veredicto lo dice y queda marcado como parcial. Un parcial nunca es `low`.

## 6. Propiedad, privacidad y ciclo de vida de los datos

- La jerarquía de propiedad es `tenant → case → document → extraction → chunk`.
  Los datos nunca pertenecen al agente, al workflow ni al worker que los creó.
- Las entidades del presunto actor y sus vínculos son memoria compartida entre
  tenants: un mismo dominio, IBAN o wallet es un solo nodo del grafo. Los casos
  que lo citan siguen siendo del tenant. Un tenant recibe de la memoria solo
  agregados (en cuántos casos y desde cuándo se vio, si alguno está
  confirmado); nunca identificadores, citas ni tenant de casos ajenos.
- La entrada breve de un aviso no se persiste íntegra. Se conservan su hash, los
  identificadores del presunto actor, las señales con la evidencia mínima y los
  vínculos necesarios para investigar reincidencias.
- Un documento aceptado para procesamiento sí necesita persistencia temporal.
  El original y las salidas completas viven como artefactos privados en el
  almacén de objetos; nunca se copian a una sesión de Agno ni a un mensaje de
  NATS. Su metadato y sus referencias viven en el caso.
- La conversación posterior vive en la sesión de Agno y caduca. La sesión solo
  conserva referencias como `tenant_id`, `case_id`, `job_id`, `document_id` y
  `extraction_id`; no es la fuente de verdad del caso.
- El acceso se concede por identidad de servicio y mínimo privilegio. No se
  comparten credenciales entre agentes, workers y runtime en producción.
- El curador opera el despliegue completo, no un tenant. Sus acciones cruzan
  tenants y cada una queda atribuida y fechada.
- Ningún aviso, documento, señal ni identificador privado de un consultante
  entra en el repositorio. El catálogo podrá contener advertencias regulatorias
  públicas aceptadas por el curador; mientras no se implemente esa ingesta, todo
  su contenido es sintético.

## 7. SurrealDB como verdad operacional

- Una instancia, dos bases: `agno/sessions` es de uso exclusivo de Agno
  (sesiones, memoria de usuario y knowledge) y `argos/ops` es el grafo
  operacional y el libro de trabajos.
- `argos/ops` contiene casos, entidades, señales, documentos, extracciones,
  chunks, trabajos, intentos, revisiones y outbox. No almacena binarios ni
  copias completas de artefactos grandes.
- Los agentes acceden a `argos/ops` solo mediante herramientas MCP acotadas. El
  MCP aplica la autorización del tenant y del caso; un agente no recibe acceso
  general a SurrealQL por comodidad.
- Los workers, que no son agentes, pueden usar un adaptador directo tipado con
  una identidad propia y permisos mínimos para reclamar trabajos y guardar sus
  resultados. Root se reserva al bootstrap.
- El esquema vive en `db/schema.surql`, es idempotente (`IF NOT EXISTS`,
  `OVERWRITE`) y lo aplica `argos/devtools/bootstrap_db.py` al arrancar el
  devcontenedor. Las tablas de entidades y aristas son `SCHEMAFULL`; la
  evidencia cruda puede ser `SCHEMALESS` cuando la fuente lo exige.
- Toda escritura va con parámetros (`$param`); nunca se interpola texto del
  consultante en SurrealQL.
- La creación de un trabajo y su comando de outbox ocurre en una misma
  transacción. La finalización de un trabajo y su evento de outbox también.
  Ningún trabajo existe solo en la cola y ningún resultado depende de publicar
  con éxito después de confirmar su estado.

## 8. AgentOS, agentes y protocolos

- Argos se sirve como un AgentOS con un gateway, agentes especialistas, equipos
  y workflows. La capacidad pública es el workflow, no cada especialista.
- El primer clúster contiene, como mínimo: `triage_agent`, `registries_agent`,
  `domain_agent`, `patterns_agent`, `memory_agent`, `document_agent`,
  `verdict_writer` y `conversation_agent`; `investigation_team` los coordina y
  `verdict_workflow` controla el ciclo completo.
- Dentro de una misma instancia de AgentOS, la coordinación se hace con los
  mecanismos de equipo y workflow de Agno. A2A se reserva para comunicación
  entre AgentOS separados o para consumidores remotos del gateway.
- Solo se publican por A2A capacidades estables y autorizadas, como analizar un
  aviso, enviar un documento, consultar un trabajo, recuperar un caso y
  conversar sobre él. Los especialistas y workers no tienen entrada pública.
- Una llamada A2A no permanece abierta mientras se procesa un trabajo pesado.
  Devuelve identificadores y estado; el resultado se consulta o se notifica al
  completarse.
- Cada agente consulta y actualiza memoria mediante herramientas MCP específicas
  para su cometido. Compartir SurrealDB no implica compartir permisos ni
  escribir libremente en las mismas tablas.

## 9. Trabajos asíncronos y NATS JetStream

- NATS JetStream es el bus de comandos y eventos de Argos. Redis queda reservado
  a servicios auxiliares que ya lo necesiten, como Langfuse; no es la cola de la
  aplicación.
- `ARGOS_JOBS` transporta comandos versionados, inicialmente
  `argos.jobs.document.extract.v1`, `argos.jobs.source.ingest.v1` y
  `argos.jobs.case.analyze.v1`. Todo análisis de caso, también el de un aviso
  breve, es un trabajo `case.analyze`: la llamada síncrona espera su estado
  terminal dentro del presupuesto y el caso sobrevive al proceso que la
  atendía. `ARGOS_EVENTS` transporta sus resultados,
  inicialmente `argos.events.document.extracted.v1`,
  `argos.events.document.failed.v1` y `argos.events.case.completed.v1`.
- El cuerpo de un mensaje contiene únicamente `job_id` y `attempt`. El estado,
  la entrada, el resultado, la autorización y la relación con el caso se vuelven
  a leer de SurrealDB.
- La entrega es al menos una vez: consumidor durable, ACK explícito después de
  persistir, reintentos con backoff e idempotencia. `Nats-Msg-Id` se deriva de
  `job_id` y `attempt`.
- Cada intento nace en SurrealDB con su comando de outbox y un arrendamiento.
  Un intento cuyo arrendamiento vence sin cerrarse se da por perdido y se
  reencola por código determinista; la reentrega propia de NATS es solo red de
  seguridad y una entrega con un intento que no es el actual se confirma sin
  efecto.
- Un documento se identifica dentro de su caso por el hash del contenido, y una
  extracción por documento, versión del extractor y opciones normalizadas. El
  mismo PDF en otro caso es otro documento con su propia extracción y
  caducidad. Reprocesar crea una versión nueva; nunca sobrescribe
  silenciosamente una extracción anterior.
- Agotados los intentos, el trabajo queda en estado terminal operable en
  SurrealDB. El curador puede inspeccionarlo y pedir un reprocesamiento; no se
  pierde en una cola muerta opaca.
- Los eventos transportan referencias, no datos de negocio. El receptor vuelve
  a comprobar permisos y recupera el resultado de la fuente de verdad.

## 10. Artefactos y workers

- RustFS es el almacén S3-compatible de Argos para originales, texto completo,
  OCR, imágenes de página y salidas grandes. El código depende del puerto
  neutral `S3ObjectStore`, no de una API específica del proveedor.
- Los buckets son privados. Los agentes no reciben credenciales S3; acceden a
  fragmentos autorizados mediante MCP. Solo el servicio de entrada y el worker
  obtienen acceso acotado a objetos, preferiblemente con URLs firmadas breves.
- El worker de documentos es stateless: reclama un trabajo, lee su definición
  de SurrealDB, obtiene el objeto, verifica hash y tamaño, extrae y persiste los
  artefactos, metadatos y evento pendiente. El dispatcher publica ese evento.
  Puede reiniciarse en cualquier paso sin perder la capacidad de reanudar.
- El original y cada derivado llevan hash, tamaño, tipo MIME, versión de
  extractor y fecha. Un resultado solo se anuncia después de que sus objetos y
  registros sean legibles.
- El borrado por retención recorre referencias de caso antes de eliminar
  objetos. Nunca se borra por nombre de bucket, prefijo ambiguo o estado local
  del worker.

## 11. Modelos y trazas

- Toda llamada a un LLM pasa por LiteLLM como endpoint compatible con OpenAI.
  Ningún SDK de proveedor aparece en el código de producto.
- OpenAI es el único proveedor externo de modelos soportado. El checkout mantiene
  `mock` para pruebas y arranque sin coste; una clave real solo vive en
  `.devcontainer/.env` y nunca en Git.
- Toda ejecución de agente, equipo, workflow o trabajo asíncrono propaga una
  correlación y traza a Langfuse por OpenTelemetry (OpenInference donde
  corresponda). El coste lo calcula LiteLLM; el runtime no lo duplica.
- Las trazas no contienen documentos completos, secretos ni datos personales:
  las observaciones de modelo llevan metadatos (modelo, tokens, coste,
  duración, identificadores), no sus mensajes. La sesión de Agno no persiste
  los mensajes de herramienta que transportan fragmentos de documento; guarda
  sus referencias.
- Los tests corren contra el modelo `mock` de LiteLLM o contra fakes. Ningún
  test gasta dinero por defecto.

## 12. Todo en el devcontenedor

- La plataforma objetivo levanta AgentOS, SurrealDB, NATS JetStream, RustFS,
  LiteLLM, Langfuse y sus dependencias dentro del compose. Tests, lint, tipos y
  servicio se ejecutan dentro; nunca desde el host.
- Abrir el devcontainer o activar su perfil `services` prepara de forma
  idempotente y arranca el producto completo, sin pasos manuales dentro del
  contenedor ni dependencias del host aparte de Docker y Compose.
- Las dependencias privadas de Langfuse están aisladas de los servicios de
  aplicación: que Langfuse use Redis u otro backend no autoriza a Argos a usarlo
  como cola o memoria.
- Sin claves reales en el repositorio: `.devcontainer/.env` local y
  `.env.example` versionado.
- Todos los puertos de desarrollo publicados al host escuchan en loopback.
- Sin imágenes ni charts de Bitnami.

## 13. Fuentes oficiales

- Cada advertencia lleva regulador, URL de origen y fecha de captura. Sin fecha
  no cuenta para el veredicto.
- Ingesta respetuosa: `User-Agent` identificado, límite de peticiones, caché y
  nunca más de una pasada al día por fuente salvo reproceso explícito.
- Una advertencia retirada se conserva con su estado. No se borra historia.
- Argos funciona de forma completa en local y no depende de un servicio remoto
  de conocimiento durante el análisis.
- El conocimiento curado —advertencias, tipologías, patrones y guías de
  actuación— tiene su fuente versionada en Git. SurrealDB es su proyección local
  para consulta, no una fuente editorial independiente.
- El corpus usa fichas Markdown bajo un vocabulario OKF cerrado. Su bundle
  `okf-graph/v1` alimenta tanto la representación humana como una proyección
  completa, atómica e identificada por revisión Git y hash en SurrealDB.
- Tipos, relaciones, propiedades y modos visuales se declaran en el perfil del
  repositorio. El runtime no interpreta Markdown ni mantiene un segundo
  vocabulario.
- Un checkout contiene el conocimiento necesario para arrancar. Actualizarlo es
  un cambio explícito y revisable del repositorio; nunca ocurre como efecto
  oculto de analizar un caso.
- Un catálogo federado se fija a una revisión inmutable y se materializa antes
  de analizar; la federación no crea una dependencia remota del runtime.
- Casos, documentos, señales privadas, revisiones de casos y datos de tenants no
  forman parte del catálogo de conocimiento.

## 14. Higiene

- Sin comentarios que describan el qué; solo un porqué no obvio, de una línea.
- `ruff`, `mypy` y `pyright` en modo estricto. `typing.Any` está prohibido en
  todo el código, incluidos adaptadores y tests; tampoco se admiten supresiones
  de tipos.
- Dependencias Python con `uv`, fijadas por rango menor. Agno 3.x, Python 3.12+.
- Commits convencionales; las migraciones de esquema son cambios en
  `db/schema.surql` con su caso en la spec, nunca sentencias sueltas.
