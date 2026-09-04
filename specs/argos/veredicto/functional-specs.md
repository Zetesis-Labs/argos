# Argos · Veredicto de un aviso

**App**: `argos` · **Iniciativa**: `veredicto` (v1)

Los flujos llevan identificador `W1`…`W5` y las reglas `R1`…`R29`. Las specs
técnicas `Sxx` los citan. Esta especificación describe el comportamiento; la
topología concreta se fija en la vertical técnica.

## 1. Resumen

Una persona recibe una oferta de inversión, un mensaje de un supuesto broker,
un enlace a una plataforma de criptomonedas o un documento relacionado con una
operación sospechosa. Argos le da una segunda opinión: qué indicios de fraude
presenta, qué evidencias los sostienen, qué entidades aparecen, si ya se habían
visto antes y qué hacer ahora y dónde acudir.

Argos no decide si algo es una estafa. Reúne indicios de fuentes oficiales, del
análisis técnico del enlace, del discurso y de su memoria de casos anteriores;
los pondera con criterios fijos y los explica en lenguaje llano. Cada caso
analizado alimenta una memoria operacional compartida por agentes
especializados.

Esta iniciativa cubre el análisis inmediato de avisos breves, el procesamiento
asíncrono de documentos PDF, la conversación posterior sobre el veredicto, la
ingesta de fuentes oficiales y la revisión de casos. Quedan fuera el canal
público final, la denuncia asistida, el asesoramiento financiero o jurídico y
la interpretación jurídica de contratos.

## 2. Actores y roles

| Actor | Quién es | Qué puede hacer |
|---|---|---|
| **Usuario** | Quien se baja Argos y lo ejecuta en su máquina. Es a la vez consultante, operador y curador: no hay más gente. | Enviar un aviso, leer el veredicto, preguntar sobre él, marcarlo revisado, explorar su memoria y curar su catálogo |
| **Cliente local** | La terminal y, en el futuro, un panel local sobre las mismas capacidades. | Invocar cualquier capacidad; no hay credencial que presentar |
| **Workflow de veredicto** | Coordinador del caso. | Validar el proceso, llamar a especialistas, esperar trabajos, puntuar y cerrar el caso |
| **Agentes especialistas** | Triaje, registros, dominio, patrones, memoria, documentos, redacción y conversación. | Ejecutar únicamente su cometido con herramientas y permisos acotados |
| **Worker de documentos** | Proceso no conversacional que transforma documentos. | Extraer texto y metadatos, ejecutar OCR cuando corresponda y guardar el resultado del trabajo |
| **Fuentes oficiales** | CNMV, FCA e IOSCO I-SCAN. | Aportar advertencias con origen y fecha; son sistemas externos de solo lectura |
| **Fuentes técnicas** | RDAP, registros de certificados y listas de reputación. | Aportar hechos sobre dominios; son sistemas externos de solo lectura |
| **Pasarela de modelos** | Servicio que sirve modelos de lenguaje. | Recibir todas las peticiones a modelos y devolver su resultado |
| **Observabilidad** | Servicio que recibe trazas y métricas. | Mostrar ejecuciones y fallos sin recibir documentos completos ni secretos |

## 3. Objetivos y trabajos del usuario

**Objetivos del producto**

- Reducir el tiempo entre «me llega algo raro» y «sé qué hacer» a menos de un
  minuto cuando la entrada ya está disponible como texto, enlace o imagen.
- Aceptar trabajos largos sin mantener abierta una llamada, perder el caso o
  depender de que sobreviva el agente que los solicitó.
- Que ningún veredicto se emita sin evidencias y sin acciones.
- Que la memoria reconozca una entidad reincidente aunque cambie el disfraz.
- Que todo lo analizado se quede en la máquina de quien ejecuta Argos y siga
  siendo suyo hasta que decida borrarlo.

**Trabajos del consultante**

- Saber si la entidad que le contacta está advertida por un regulador.
- Saber si el enlace recibido presenta señales técnicas de fraude.
- Entender los patrones de manipulación del mensaje o del texto extraído.
- Enviar un PDF y poder cerrar la llamada mientras Argos lo procesa.
- Saber en qué estado está el procesamiento y recibir el veredicto al terminar.
- Saber qué hacer ahora: no pagar, bloquear, denunciar o hablar con el banco.
- Preguntar dudas sobre el veredicto con el mismo contexto.

**Trabajos del usuario al operar Argos**

- Ver casos recientes, nivel de riesgo y trabajos pendientes o fallidos.
- Confirmar o descartar casos para mejorar la memoria revisada.
- Reintentar un procesamiento sin duplicar resultados ni sobrescribir historia.
- Saber si las fuentes están al día y actuar cuando una ingesta falla.
- Explorar qué entidades se repiten y qué vínculos hay entre casos.

**Trabajos de un servicio remoto**

- Delegar una capacidad completa de Argos sin conocer sus agentes internos.
- Recibir identificadores estables y consultar el resultado de forma autorizada.
- Correlacionar el caso remoto con su propia ejecución sin transportar el PDF o
  el texto completo en mensajes de coordinación.

## 4. Puntos de entrada y salida

| Punto | Actor | Dirección | Contenido y resultado |
|---|---|---|---|
| **Analizar aviso** (CLI/API local) | Usuario | entra/sale | Texto, hasta tres enlaces y una imagen opcional; devuelve el caso con su veredicto y la evidencia que lo sostiene |
| **Enviar documento** (API local) | Usuario | entra/sale | PDF asociado a un caso nuevo o a uno existente sin veredicto; devuelve caso, documento, trabajo y estado aceptado |
| **Consultar trabajo** (API local) | Usuario | sale | Estado, intento, progreso disponible, error y referencias al resultado |
| **Consultar caso** (CLI/API local) | Usuario | sale | Estado actual, error si lo hubo y, cuando existe, veredicto con sus indicios |
| **Conversar** (CLI/API local) | Usuario | entra/sale | Preguntas sobre el veredicto dentro de una sesión del caso |
| **Ingesta programada** | Sistema | entra | Una ejecución diaria por fuente oficial |
| **Revisar caso** (CLI/API local) | Usuario | entra | Marca de revisión y nota opcional |
| **Reprocesar trabajo** (API local) | Usuario | entra | Nueva ejecución versionada a partir del mismo documento |
| **Explorar memoria** (CLI/API local) | Usuario | sale | Consultas sobre entidades, vínculos y casos |
| **Evento de resultado** | Argos | sale | Referencias a caso, trabajo y resultado; nunca el documento ni su texto completo |

Fuera de esta iniciativa: web pública, Telegram, WhatsApp y exportaciones de
artefactos. Esos canales consumirán los mismos contratos en iniciativas
posteriores.

## 5. Flujos

### W1 · Analizar un aviso

**Inicio**: llega un aviso por API, A2A o CLI.

1. Se valida el aviso antes de crear nada.
2. Se validan los límites de R1. Si falla, se rechaza con el motivo y no se crea
   caso.
3. Se crea el caso en `received` con el hash del aviso y, en la misma operación
   durable, el trabajo de análisis que lo llevará a un estado terminal (R12).
   Si R9 encuentra un caso equivalente, se devuelve aquel caso, terminado o en
   curso, sin repetir el análisis.
4. El trabajo de análisis arranca y el caso pasa a `analyzing`. Triaje extrae y
   normaliza identificadores, transcribe la imagen, detecta el idioma y propone
   tipologías.
5. El workflow solicita en paralelo cuatro análisis, cada uno limitado a su
   cometido:
   - **Registros**: coincidencias con advertencias oficiales, incluidos clones.
   - **Dominio**: registro, certificado, reputación y similitud con marcas.
   - **Patrones**: técnicas de manipulación sostenidas por una cita.
   - **Memoria**: apariciones previas de los identificadores y revisión asociada.
6. Se descartan las señales sin evidencia, se fusionan las restantes y el
   núcleo determinista calcula el nivel conforme a R4.
7. El redactor compone el veredicto conforme a R5–R7 y R14, sin poder cambiar el
   nivel calculado.
8. Se guardan el caso, entidades, señales, vínculos y veredicto conforme a R8.
   El caso pasa a `verdict_issued` o `partial`.
9. Se devuelve el veredicto y, si el origen fue remoto, su identificador de
   correlación.

**Fin**: existe un veredicto con nivel, evidencias y acciones.

**Caminos alternativos**

- Si no hay identificadores ni texto útil, termina en `insufficient` y pide una
  entrada más completa; no se puntúa.
- Si la imagen es ilegible, sigue con el resto; sin resto, termina como
  `insufficient`.
- Si un enlace redirige, origen y destino se analizan como entidades distintas.
- Si una fuente no responde o se agota el tiempo, se emite `partial` con lo
  obtenido y se indica qué faltó.
- Ante fallo interno, termina en `failed` y el error se muestra entero: quien lo
  recibe es quien ejecuta Argos y necesita saber qué se rompió.
- Si el proceso que atendía la llamada se reinicia a mitad, el trabajo de
  análisis se reentrega y el caso termina igualmente; el cliente lo recupera
  con «Consultar caso» (R25).

### W2 · Conversar sobre un veredicto

**Inicio**: un actor autorizado escribe en la sesión de un caso con veredicto.

1. Se verifica que la sesión corresponde a ese caso.
2. La respuesta se apoya en el veredicto y sus evidencias y puede consultar la
   memoria autorizada para ampliar.
3. Si aporta datos nuevos, se ofrece analizarlos como caso nuevo vinculado en
   vez de modificar el veredicto emitido.
4. La conversación no cambia el nivel ni escribe nuevas señales.

**Fin**: la duda se responde con las evidencias existentes o se deriva a un caso
nuevo.

**Caminos alternativos**

- Si pide asesoramiento financiero o jurídico, se declina y se recuerda el
  alcance.
- Si pide afirmar que alguien estafa, se mantiene el lenguaje de indicios.
- Si el artefacto completo ya caducó, se responde con el veredicto y la evidencia
  conservada y se avisa de que el original ya no está disponible.

### W3 · Curar fuentes oficiales

**Inicio**: el usuario solicita actualizar una fuente o revisa una propuesta
generada de forma programada.

1. Se crea un trabajo por fuente. Cada fuente se descarga respetando límite,
   caché e identificación del cliente.
2. Se normaliza cada entrada: regulador, entidad, identificadores, tipo,
   condición de clon, fecha de publicación, URL y fecha de captura.
3. El resultado se compara con el catálogo versionado: las entradas nuevas se
   proponen, las existentes se actualizan sin perder su primera captura y las
   ausentes se proponen como retiradas, nunca como borradas.
4. El usuario acepta o rechaza los cambios antes de incorporarlos al catálogo.
5. Los cambios aceptados quedan revisables en Git y se cargan en la SurrealDB
   local que consultan los agentes.
6. Se registra inicio, fin, nuevas, actualizadas, retiradas e incidencia.

**Fin**: el catálogo versionado y su proyección local reflejan la última
actualización aceptada de cada fuente.

**Caminos alternativos**

- Una fuente caída o con formato cambiado conserva la última ingesta buena y
  deja el trabajo fallido a la vista.
- Una ingesta con menos del 50 % de las entradas anteriores se rechaza como
  posible descarga parcial.

### W4 · Revisar un caso

**Inicio**: el usuario abre un caso con veredicto.

1. Ve el veredicto, las señales, su evidencia, entidades, trabajos y fuentes que
   no respondieron.
2. Marca `confirmed`, `false_positive` o `inconclusive`, con nota opcional.
3. La marca afecta a análisis futuros conforme a R4 y R10.

**Fin**: queda una revisión atribuida y fechada.

### W5 · Procesar un documento

**Inicio**: un cliente autorizado envía un PDF para un caso nuevo o existente.

1. Se valida la parte barata de R19 (extensión, tipo
   declarado, firma real y tamaño) antes de aceptar. Un caso existente solo
   admite documentos mientras no tenga veredicto; si ya lo tiene, se crea un
   caso nuevo vinculado al anterior (R12).
2. El original se registra como documento del caso, se calcula su
   hash y se guarda como artefacto privado. El caso pasa a
   `awaiting_processing`: un caso con documentos pendientes no se analiza hasta
   que todos terminen (R15).
3. En la misma operación durable se crean el trabajo `queued` y la orden
   pendiente de publicación. Si el mismo caso ya tenía un documento con ese
   hash, se devuelven el documento y el trabajo existentes sin crear nada
   (R22). La respuesta inmediata contiene `case_id`, `document_id`, `job_id` y
   la forma de consultar el estado.
4. El worker reclama el trabajo, verifica que sigue autorizado y pasa a
   `running`. Completa la validación profunda de R19 (cifrado, contenido
   activo, páginas); incumplirla es un fallo permanente con código estable.
   Extrae texto por página y metadatos; aplica OCR únicamente a las páginas sin
   texto utilizable.
5. Guarda la extracción completa como artefacto privado y produce chunks con
   página y posición. En la misma confirmación marca `completed` y deja pendiente
   la notificación referenciada; su publicación puede recuperarse tras un fallo.
6. Cuando termina el último documento pendiente del caso se crea su trabajo de
   análisis (R12), que recupera los chunks autorizados, ejecuta W1 desde el
   triaje y guarda las señales con citas y páginas. El tiempo de R15 empieza
   entonces.
7. La sesión que originó el trabajo puede desaparecer: el caso se reanuda desde
   su estado durable y el resultado se consulta por identificador.

**Fin**: el documento tiene una extracción versionada y el caso un veredicto, o
el trabajo queda en un estado terminal explicable.

**Caminos alternativos**

- Un PDF que no supera la validación barata se rechaza antes de encolar. Uno
  cifrado, corrupto, con contenido activo o que excede páginas termina como
  fallo permanente del trabajo, con código estable y sin reintentos. En ambos
  casos el documento queda `rejected`.
- Una entrega duplicada del mismo intento no duplica la extracción.
- Un fallo transitorio reintenta con backoff; cada intento queda registrado. Un
  worker que muere a mitad deja un intento perdido que se reencola por sí solo
  (R21).
- Agotado el presupuesto, el trabajo termina `failed`. Si el caso no tiene
  ninguna otra entrada analizable termina `failed`; si la tiene, se analiza y el
  veredicto es `partial` e indica que el documento no se procesó. El caso no
  finge un veredicto y se puede reprocesar.
- Reprocesar un caso `failed` o `partial` lo devuelve a `awaiting_processing`
  con un trabajo nuevo; el veredicto anterior se conserva como versión superada
  (R12, R22).
- Reprocesar con otro extractor crea una extracción nueva y conserva la anterior.
- Enviar un documento a un caso que ya tiene veredicto crea un caso nuevo
  vinculado al anterior; no modifica el veredicto emitido.

## 6. Reglas y restricciones funcionales

- **R1 · Límites del aviso breve.** Texto de hasta 20.000 caracteres, hasta tres
  enlaces y una imagen de hasta 10 MB en PNG, JPEG o WebP. Excederlos rechaza el
  aviso con el motivo exacto.
- **R2 · Identificadores normalizados.** Dominios en minúsculas y reducidos al
  dominio registrable; teléfonos en formato internacional, con España por
  defecto si falta prefijo; emails en minúsculas; IBAN sin espacios y con
  dígitos de control válidos; wallets Bitcoin, Ethereum compatibles y Tron;
  handles con su red; nombres de empresa tal como aparecen. Dominio, teléfono,
  email, IBAN, wallet y handle son fuertes; el nombre de empresa es débil.
- **R3 · Evidencia obligatoria.** Toda señal lleva fuente, fecha de observación,
  valor y cita, página o enlace que la sostiene. Una señal sin evidencia se
  descarta antes de puntuar.
- **R4 · Niveles de riesgo.** Existen `low`, `medium`, `high` y `critical`:
  - una coincidencia oficial vigente sobre identificador fuerte o nombre exacto
    da `critical`;
  - una reincidencia fuerte de un caso `confirmed` da `critical`;
  - dos señales fuertes de análisis distintos dan al menos `high`;
  - una señal fuerte o tres débiles dan al menos `medium`;
  - `low` exige que todos los análisis aplicables respondan y ninguno produzca
    señal fuerte.
  Además existe `undetermined`, que no es un nivel de riesgo sino la
  declaración de que no se pudo evaluar: se reserva para un veredicto `partial`
  sin ninguna señal. Los pesos concretos se calibran técnicamente; estas
  garantías no se rebajan.
- **R5 · Degradación explícita.** Si un análisis aplicable no termina, el
  veredicto es `partial`, dice qué faltó y nunca puede ser `low`. Un documento
  cuya extracción falló cuenta como análisis que no terminó. Un `partial` sin
  ninguna señal lleva nivel `undetermined`.
- **R6 · Lenguaje de indicios.** Se habla de indicios, señales y coincidencias.
  Nunca se imputa un delito ni se señala a una persona física como estafadora.
- **R7 · Acciones siempre.** Todo veredicto termina con acciones ordenadas por
  urgencia y dónde acudir. En `high` y `critical`: no enviar dinero ni datos,
  cortar contacto, avisar al banco si hubo pago, denunciar y comunicar a CNMV.
  En `undetermined`: no enviar dinero ni datos y repetir la consulta más tarde.
- **R8 · Privacidad.** El aviso breve no se persiste íntegro. Se guardan hash,
  idioma, estado, nivel, tipologías, identificadores, vínculos y la mínima cita
  que sostiene cada señal. Un PDF y su extracción completa son artefactos
  privados con caducidad; nunca se copian a conversaciones, eventos ni trazas.
  Los fragmentos que un agente consulta no se guardan en su sesión, solo sus
  referencias; las trazas registran metadatos de las llamadas a modelos, no sus
  mensajes. Los nombres de personas físicas no se persisten como entidades.
- **R9 · Repetir es analizar de nuevo.** Enviar dos veces el mismo aviso abre
  dos casos. Deduplicar ahorraría un análisis y a cambio devolvería un caso ya
  cerrado, o uno fallido, en lugar de mirar otra vez; en local no hay coste que
  racionar.
- **R10 · Vínculos.** Dos entidades se vinculan como «mismo actor» solo si
  comparten un identificador fuerte. Solo compartir empresa no basta. Un caso
  `false_positive` no crea reincidencia.
- **R11 · Fuentes con fecha.** Toda advertencia lleva regulador, URL y fecha de
  captura. Sin captura no participa. Una retirada se conserva como histórica
  pero no produce coincidencia vigente. El catálogo aceptado se versiona en Git
  y se consulta desde una proyección local.
- **R12 · Estados del caso.** `received` pasa a `awaiting_processing` cuando
  tiene documentos pendientes y a `analyzing` cuando arranca su trabajo de
  análisis. Todo caso pasa por `analyzing` mediante un trabajo durable, también
  el de un aviso breve. Los terminales son `verdict_issued`, `partial`,
  `insufficient` y `failed`. Solo un reproceso explícito saca a un caso de un
  terminal, y conserva el veredicto anterior como versión superada. Un documento enviado a un caso con
  veredicto crea un caso vinculado; un reintento de extracción no.
- **R13 · Estados de revisión.** `unreviewed` pasa a `confirmed`,
  `false_positive` o `inconclusive`, y la marca se puede cambiar conservando su
  fecha. Es la única fuente de la reincidencia: sin ella la memoria sabe que un
  identificador se repite, pero no que alguna vez fue fraude.
- **R14 · Idioma.** Español o inglés siguen el idioma de la entrada; cualquier
  otro idioma produce veredicto en español. Las citas mantienen su original.
- **R15 · Tiempo.** W1 tarda como máximo 60 segundos desde que toda entrada
  necesaria es analizable. W5 es asíncrono y no consume ese presupuesto.
- **R16 · Ejecución local.** Argos corre en la máquina de su usuario y no
  autentica a nadie: no hay clientes ajenos de los que aislarse ni roles que
  distinguir. Nada de lo que analiza sale de esa máquina. El mínimo privilegio
  se aplica hacia dentro, para limitar lo que un prompt puede provocar.
- **R17 · Escrituras acotadas.** W1 escribe caso, entidades, señales y veredicto;
  W3 propone cambios al catálogo curado y actualiza su proyección local; W5
  escribe documentos, trabajos, extracciones y chunks; W4 escribe revisiones.
  W2 solo lee y escribe su sesión.
- **R18 · Retención.** Casos, señales y citas mínimas duran 12 meses; sesiones,
  originales, extracciones completas y chunks duran 30 días por defecto;
  advertencias oficiales e ingestas se conservan sin límite. El borrado elimina
  también artefactos sin referencias vivas.
- **R19 · Documento admitido.** En v1 solo PDF, máximo 25 MB y 500 páginas.
  Antes de encolar se valida extensión, tipo declarado, firma real y tamaño.
  Cifrado, corrupción, contenido activo no admitido y exceso de páginas los
  detecta el worker: son fallos permanentes con código estable y sin
  reintentos, y dejan el documento `rejected`.
- **R20 · Aceptación asíncrona.** Enviar un documento devuelve aceptación y los
  identificadores de caso, documento y trabajo; no espera a la extracción ni
  promete un veredicto inmediato.
- **R21 · Estados del trabajo.** `queued` → `running` → `completed` o `failed`.
  Cada intento tiene inicio, fin, arrendamiento y error categorizado. Un intento
  que agota su arrendamiento sin cerrarse se considera perdido: el trabajo
  vuelve a `queued` con un intento nuevo mientras quede presupuesto y, si no,
  termina `failed`.
- **R22 · Idempotencia.** Un documento se identifica dentro de su caso por el
  hash del contenido: enviar el mismo PDF al mismo caso devuelve el documento y
  el trabajo existentes. Una extracción se identifica por documento, versión de
  extractor y opciones normalizadas; repetir el mismo intento devuelve el mismo
  resultado. Reprocesar cambia la versión o crea una orden explícita y conserva
  historia. El mismo PDF en casos distintos son documentos y extracciones
  distintos, cada uno con su caducidad.
- **R23 · Propiedad.** Documento y extracción pertenecen al caso, no al agente
  ni al worker. Una sesión conserva referencias, nunca la única copia.
- **R24 · Resultado referenciado.** Una notificación contiene identificadores y
  estado. El consumidor vuelve a consultar el caso con su identidad; no recibe
  el PDF ni el texto completo en la notificación.
- **R25 · Reinicio seguro.** Reiniciar un agente, workflow, worker o consumidor
  no pierde trabajo confirmado ni obliga a repetir una extracción completada.
- **R26 · Separación de agentes.** Triaje descubre la forma de la entrada;
  registros, dominio, patrones y memoria producen señales de su área;
  documentos coordina trabajos; redacción explica un nivel ya calculado;
  conversación responde sin alterar el caso. Ningún especialista suplanta a
  otro por fallo.
- **R27 · A2A de capacidades.** A2A expone capacidades del gateway, no agentes
  internos ni workers. Un trabajo largo devuelve estado y referencias; no
  mantiene una sesión remota abierta como mecanismo de fiabilidad.
- **R28 · Errores visibles y operables.** Todo fallo termina en un estado, código
  público y correlación. Los errores no se convierten en respuestas vacías ni se
  pierden después de agotar reintentos.
- **R29 · Memoria entre casos.** Las entidades y sus vínculos son globales: un
  mismo identificador es un solo nodo y sobrevive al caso que lo descubrió. La
  memoria se consulta como agregado —en cuántos casos y desde cuándo se vio, si
  alguno está `confirmed`— porque es lo que sostiene una decisión; el detalle
  está en los casos, que son del usuario.

## 7. Conceptos de datos

| Concepto | Qué es | Campos con significado | Estados |
|---|---|---|---|
| **Tenant** | Frontera de propiedad y autorización | identificador, identidad de servicio, política de retención | activo, suspendido |
| **Aviso** | Entrada breve del consultante | texto, enlaces, imagen, idioma detectado | no se persiste íntegro |
| **Caso** | Unidad de investigación | aviso, fecha, idioma, nivel, parcial, tipologías, revisión, error, caso anterior | R12, R13 |
| **Documento** | PDF asociado a un caso | caso, hash, MIME, tamaño, páginas, referencia privada, caducidad | accepted, rejected, expired |
| **Trabajo** | Encargo durable de procesamiento | tipo (`document.extract`, `source.ingest`), caso, documento, intento actual, presupuesto de intentos, arrendamiento, versión, fechas, error | R21 |
| **Intento** | Una ejecución de un trabajo | número, consumidor, inicio, fin, arrendamiento, error categorizado | running, succeeded, failed, lost |
| **Extracción** | Resultado versionado de un documento | documento, extractor, versión, hash, páginas, referencia privada, calidad, fecha | available, superseded, expired |
| **Chunk** | Fragmento recuperable con contexto | extracción, página, posición, texto, hash, caducidad | available, expired |
| **Entidad** | Identificador del presunto actor, compartido entre casos | tipo, valor normalizado, primera y última aparición, número de casos, casos confirmados | R29 |
| **Señal** | Indicio sobre un caso | tipo, fuerza, análisis, fuente, fecha, valor, evidencia, página, entidad | — |
| **Advertencia oficial** | Entrada de un regulador | regulador, entidad, identificadores, tipo, clon, fechas, URL | vigente, retirada |
| **Tipología** | Familia de fraude | nombre, descripción, patrones, acciones | — |
| **Patrón** | Técnica de manipulación | nombre, fuerza, ejemplos | — |
| **Veredicto** | Salida explicada del caso | versión, nivel, parcial, resumen, señales, entidades, reincidencia, acciones, fuentes ausentes | vigente, superado |
| **Vínculo** | Relación del grafo | tipo, origen, destino, caso, fecha | — |
| **Revisión** | Juicio del usuario sobre un caso | marca, nota, fecha | R13 |
| **Ejecución de ingesta** | Pasada sobre una fuente | fuente, fechas, recuentos, error | ok, error, rechazada |
| **Guía de acciones** | Recomendaciones por nivel y tipología | nivel, tipología, acciones ordenadas, dónde acudir | — |

## 8. Representación gráfica

Argos v1 se consume por API o A2A. Un dashboard local de desarrollo podrá
presentar en el futuro sus capacidades y la conversación con el agente sin
convertirse en un canal público. El estado mínimo visible de un documento es:

```text
aceptado → en cola → procesando → extracción disponible → analizando → veredicto
                               ↘ fallo reintentable ↗
                               ↘ fallo terminal → reprocesar
```

## 9. Restricciones y decisiones de alcance

- Los avisos breves aceptan texto, enlaces e imagen; el pipeline asíncrono v1
  acepta únicamente PDF. Audio, vídeo y formatos ofimáticos quedan fuera.
- Procesar un contrato significa extraer y analizar indicios del texto; no
  interpretar su validez jurídica.
- El worker transforma documentos; no puntúa, redacta ni se expone por A2A.
- No verifica identidades personales ni consulta registros mercantiles.
- Fuentes oficiales iniciales: CNMV, FCA e I-SCAN.
- Solo español e inglés como idioma de salida directo.
- Catálogo de tipologías y guía de acciones son contenido curado, no se
  autoaprenden.
- No es asesoramiento financiero ni jurídico, y el veredicto lo dice.

## 10. Preguntas abiertas y asunciones

**Asunciones operativas para v1**

- **A1** · El nombre del producto es `argos`.
- **A2** · El canal inicial es API/A2A con credencial de servicio; la web y la
  mensajería son iniciativas posteriores.
- **A3** · CNMV se obtiene de sus fuentes publicadas; FCA e I-SCAN se integran
  con el mecanismo oficial disponible al implementar cada fuente.
- **A4** · Argos no tiene cuentas: se distribuye como un devcontainer y su
  único usuario es quien lo ejecuta. Decidido el 2026-09-04.
- **A5** · Nada caduca solo: un análisis local se guarda hasta que su dueño lo
  borra. R18 vuelve si Argos se ofrece alguna vez como servicio alojado.
- **A6** · R1, R15 y R19 son valores iniciales configurables hacia abajo, nunca
  hacia arriba sin una nueva decisión.
- **A7** · Las imágenes breves usan visión por la pasarela; el PDF usa extracción
  determinista y OCR de respaldo en el worker.
- **A8** · El worker de documentos se implementa en Python dentro del
  repositorio, con los mismos puertos, fakes y anclaje de specs que el resto.
  Su comportamiento lo fija W5; extraerlo a otro lenguaje exigiría definir
  antes cómo se ancla a la spec.
- **A9** · La consulta de trabajo es el mecanismo obligatorio; una notificación
  push posterior es una comodidad y no la fuente de verdad.
- **A10** · La memoria de entidades se comparte entre casos y se consulta como
  agregados (R29). Decidido el 2026-09-03, sin tenants desde el 2026-09-04.
- **A11** · Revisar un caso es del usuario y no exige rol alguno (R13, R16).
- **A12** · El análisis de un aviso breve ocurre dentro de la llamada que lo
  pide: cabe en el presupuesto de R15 y no necesita ser un trabajo durable. Lo
  será cuando la entrada deje de caber, empezando por los documentos (R12, R25).
- **A14** · El aviso se conserva en su caso mientras dure la retención. Sin su
  texto no se puede analizar, explicar ni reproducir un veredicto (R1, R8).
- **A13** · `undetermined` existe para no fingir un nivel cuando un parcial no
  reunió ninguna señal (R4, R5).

**Preguntas abiertas no bloqueantes para iniciar la plataforma**

- **Q1** · Resuelta el 2026-09-04: el aviso es del usuario y lo ve entero. La
  pregunta vuelve si Argos se ofrece alguna vez como servicio alojado.
- **Q2** · ¿Qué fuente oficial se usará para Banco de España y DGSFP?
- **Q3** · ¿Se publicarán estadísticas agregadas? Requiere una revisión de
  privacidad independiente.
- **Q4** · ¿Qué umbral de similitud con marcas cuenta como señal fuerte de clon?
- **Q5** · ¿Los límites de 25 MB, 500 páginas y 30 días se mantienen tras medir
  documentos reales?
- **Q6** · ¿Qué clientes necesitan webhook de finalización además de consulta de
  estado o A2A?
