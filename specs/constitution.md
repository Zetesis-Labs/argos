# Constitución de Argos

Principios que toda especificación, plan y cambio de código de este repositorio
deben respetar. Si una spec o un cambio los contradice, se cambia la spec o el
cambio, no la constitución; y si hay que cambiar la constitución, se hace en un
commit propio que lo diga.

Argos analiza avisos relacionados con posibles fraudes financieros y devuelve un
veredicto explicado con evidencias. Se ejecuta como un servicio con memoria
operacional compartida y agentes especializados de apoyo.

**Argos se distribuye como un devcontainer que alguien se baja y ejecuta en su
propia máquina.** Hay un solo usuario: quien lo arranca. Todo lo que dé por
supuesto que Argos es un servicio compartido —clientes, credenciales, roles,
aislamiento entre organizaciones— contradice el producto y sobra.

Es además una prueba de concepto deliberadamente pequeña: un aviso de texto y el
catálogo curado. Las secciones que describen documentos, cola durable y almacén
de objetos fijan cómo se harán esas piezas cuando el producto las necesite;
hasta entonces no existen en el código.

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
- `argos/agents` declara los agentes de Agno y los cablea con herramientas.
  Ninguna regla de negocio vive en un prompt.

## 4. El LLM no puntúa ni gobierna el proceso

- El nivel de riesgo lo decide `core.score` a partir de señales tipadas. El LLM
  interpreta, propone señales y redacta; no asigna niveles.
- Las marcas que por sí solas llevan a `critical` —advertencia oficial vigente y
  reincidencia confirmada— las pone el código consultando el catálogo y la
  memoria. Un informe de un modelo que las declare se ignora.
- Una señal solo cuenta si su cita aparece literalmente en el aviso.
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

- Todo lo que Argos guarda pertenece a quien lo ejecuta y no sale de su máquina.
  La jerarquía es `case → document → extraction → chunk` cuando existan
  documentos. Los datos nunca pertenecen al agente ni al proceso que los creó.
- Las entidades del presunto actor y sus vínculos son memoria entre casos: un
  mismo dominio, IBAN o wallet es un solo nodo del grafo y sobrevive al caso que
  lo descubrió. La memoria se consulta como agregado —en cuántos casos y desde
  cuándo se vio, si alguno está confirmado— porque es lo útil para decidir, no
  porque haya de quién protegerlo.
- El aviso se persiste en su caso con su hash, sus identificadores, sus señales
  con la evidencia mínima y sus vínculos: sin el texto no hay nada que analizar
  ni forma de reproducir un veredicto. Caduca con el caso y no se copia a una
  sesión de Agno, a un log ni a una traza.
- Un documento aceptado para procesamiento necesitará persistencia temporal.
  El original y las salidas completas vivirán como artefactos privados en el
  almacén de objetos; nunca se copiarán a una sesión de Agno. Su metadato y sus
  referencias viven en el caso.
- La conversación posterior vive en la sesión de Agno y caduca. La sesión solo
  conserva referencias como `case_id`, `document_id` y `extraction_id`; no es la
  fuente de verdad del caso.
- Argos no autentica a nadie: escucha en loopback dentro del contenedor de su
  usuario. El mínimo privilegio sigue aplicando hacia dentro —el proceso entra
  en SurrealDB con su usuario y el de los agentes es de solo lectura— porque
  limita lo que un prompt puede provocar, no lo que un intruso puede leer.
- Nada caduca solo. Un análisis local se guarda hasta que su dueño lo borra.
- Ningún aviso, documento, señal ni identificador de un caso entra en el
  repositorio. El catálogo podrá contener advertencias regulatorias públicas
  aceptadas por quien mantiene la distribución; mientras no se implemente esa
  ingesta, todo su contenido es sintético.

## 7. SurrealDB como verdad operacional

- Una instancia, dos bases: `agno/sessions` es de uso exclusivo de Agno
  (sesiones, memoria de usuario y knowledge) y `argos/ops` es el grafo
  operacional y el libro de trabajos.
- `argos/ops` contiene casos, entidades, señales, veredictos, revisiones y la
  proyección del catálogo. No almacena binarios ni copias completas de
  artefactos grandes.
- Un agente accede a `argos/ops` solo mediante herramientas acotadas por su
  capacidad y su caso. Nunca recibe SurrealQL general por comodidad, ni por una
  herramienta propia ni por el MCP de SurrealDB.
- El proceso entra con su propia identidad de base de datos y permisos mínimos.
  La de los agentes es de solo lectura. Root se reserva al bootstrap.
- El esquema vive en `db/schema.surql`, es idempotente (`IF NOT EXISTS`,
  `OVERWRITE`) y lo aplica `argos/devtools/bootstrap_db.py` al arrancar el
  devcontenedor. Las tablas de entidades y aristas son `SCHEMAFULL`; la
  evidencia cruda puede ser `SCHEMALESS` cuando la fuente lo exige.
- Toda escritura va con parámetros (`$param`); nunca se interpola texto del
  consultante en SurrealQL.
- Una transición de estado es una escritura condicional por revisión dentro de
  una transacción. Dos procesos no pueden analizar el mismo caso a la vez.

## 8. Gateway, agentes y protocolos

- Argos se sirve como un gateway HTTP con agentes de apoyo. La capacidad pública
  es la del gateway, nunca un especialista.
- Un agente se añade cuando hay una decisión que un modelo hace mejor que el
  código, no para completar un organigrama. Lo que se puede reconocer con una
  expresión regular o una consulta no lleva agente.
- El clúster actual es `triage_agent`, `patterns_agent`, `verdict_writer` y
  `conversation_agent`. Coordinarlos es código: no hay equipo ni workflow de
  Agno mientras dos agentes basten.
- La superficie principal es la terminal: `argos` analiza, muestra, pregunta y
  revisa. La API HTTP local sirve las mismas capacidades para lo que venga
  después. Los especialistas no tienen entrada propia ni tarjeta de agente.
- Cuando un trabajo deje de caber en la llamada, la respuesta devolverá
  identificadores y estado en lugar de mantener la conexión abierta.
- Cada agente recibe solo las herramientas de su cometido. Compartir SurrealDB
  no implica compartir permisos ni escribir libremente en las mismas tablas.

## 9. Trabajo durable, cuando haga falta

- **Nada se procesa fuera de la llamada mientras quepa dentro.** Un aviso de
  texto se analiza en el proceso que lo atiende. Un trabajo durable se
  introduce cuando una entrada excede el presupuesto de la llamada, no antes.
- Cuando llegue ese momento, la cola es NATS JetStream. Redis queda reservado a
  servicios auxiliares que ya lo necesiten; no es la cola de la aplicación.
- El cuerpo de un mensaje contendrá únicamente `job_id` y `attempt`. El estado,
  la entrada, el resultado, la autorización y la relación con el caso se vuelven
  a leer de SurrealDB.
- La entrega será al menos una vez: consumidor durable, ACK explícito después de
  persistir, reintentos con backoff e idempotencia.
- La creación de un trabajo y su comando de outbox ocurrirán en una misma
  transacción, igual que su finalización y su evento. Ningún trabajo existirá
  solo en la cola.
- Un documento se identificará dentro de su caso por el hash del contenido, y
  una extracción por documento, versión del extractor y opciones normalizadas.
  Reprocesar creará una versión nueva; nunca sobrescribe silenciosamente una
  extracción anterior.
- Un trabajo agotado quedará en estado terminal operable en SurrealDB, nunca en
  una cola muerta opaca.

## 10. Artefactos, cuando haya documentos

- Argos no guarda binarios hoy. Cuando acepte documentos, el almacén será
  S3-compatible (RustFS en local) detrás de un puerto neutral `S3ObjectStore`,
  no de la API de un proveedor.
- Los buckets serán privados. Los agentes no reciben credenciales S3; acceden a
  fragmentos autorizados mediante sus herramientas.
- El worker que extraiga será stateless: relee su definición de SurrealDB,
  verifica hash y tamaño, y persiste artefactos, metadatos y evento pendiente.
  Podrá reiniciarse en cualquier paso sin perder la capacidad de reanudar.
- El original y cada derivado llevarán hash, tamaño, tipo MIME, versión de
  extractor y fecha. Un resultado solo se anuncia cuando es legible.
- El borrado por retención recorrerá referencias de caso antes de eliminar
  objetos. Nunca por nombre de bucket, prefijo ambiguo o estado local.

## 11. Modelos y trazas

- Toda llamada a un LLM pasa por LiteLLM como endpoint compatible con OpenAI.
  Ningún SDK de proveedor aparece en el código de producto.
- OpenAI es el único proveedor externo de modelos soportado. El checkout mantiene
  `mock` para pruebas y arranque sin coste; una clave real solo vive en
  `.devcontainer/.env` y nunca en Git.
- Toda ejecución propaga un `correlation_id` desde la llamada hasta el
  veredicto. El coste lo calcula LiteLLM; el runtime no lo duplica.
- Argos no despliega hoy backend de trazas: la observabilidad es el libro
  operacional y lo que LiteLLM registra. Cuando se añada uno, será por
  OpenTelemetry y bajo la regla siguiente.
- Un error se muestra entero, con su tipo y su mensaje: el destinatario es quien
  ejecuta Argos y esconderle el motivo solo le impide arreglarlo. Lo que no entra
  en un log ni en una traza son secretos y el contenido del aviso. La sesión de
  Agno guarda referencias, no contenido.
- Los tests corren contra el modelo `mock` de LiteLLM o contra fakes. Ningún
  test gasta dinero por defecto.

## 12. Todo en el devcontenedor

- El compose levanta lo que Argos usa y nada más: hoy SurrealDB, LiteLLM y el
  contenedor de trabajo, más SurrealDB de test y el explorador del catálogo bajo
  su perfil. Tests, lint, tipos y servicio se ejecutan dentro; nunca desde el
  host.
- Un servicio entra en el compose cuando hay código que lo usa. Una pieza de
  infraestructura que solo sostiene a otra pieza de infraestructura es señal de
  que sobran las dos.
- Abrir el devcontainer o activar su perfil `services` prepara de forma
  idempotente y arranca el producto completo, sin pasos manuales dentro del
  contenedor ni dependencias del host aparte de Docker y Compose.
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
- Casos, documentos, señales y revisiones no forman parte del catálogo de
  conocimiento.

## 14. Higiene

- Sin comentarios que describan el qué; solo un porqué no obvio, de una línea.
- `ruff`, `mypy` y `pyright` en modo estricto. `typing.Any` está prohibido en
  todo el código, incluidos adaptadores y tests; tampoco se admiten supresiones
  de tipos.
- Dependencias Python con `uv`, fijadas por rango menor. Agno 3.x, Python 3.12+.
- Commits convencionales; las migraciones de esquema son cambios en
  `db/schema.surql` con su caso en la spec, nunca sentencias sueltas.
