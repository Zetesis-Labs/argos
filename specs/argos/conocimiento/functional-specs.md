# Argos · Fundación de conocimiento curado

**App**: `argos` · **Iniciativa**: `conocimiento` (v1)

Esta especificación describe el comportamiento del catálogo que acompaña a cada
checkout de Argos. Los flujos llevan identificador `W1`…`W4` y las reglas
`R1`…`R14`; la vertical técnica de conocimiento los cita como
`conocimiento/Wn` y `conocimiento/Rn` para distinguirlos del veredicto.

## 1. Resumen

Argos distribuye el conocimiento público y curado con el código para que una
persona pueda inspeccionarlo, revisarlo y usarlo localmente sin depender de una
base de datos ni de un servicio remoto controlado por otra persona. El catálogo
se presenta como fichas enlazadas y como un grafo navegable, y se transforma en
una proyección local que los agentes consultan durante el análisis.

Esta iniciativa funda el contrato del corpus, su validación, representación y
proyección. No incluye todavía la descarga de fuentes oficiales, un editor
visual, el dashboard AG-UI ni conocimiento regulatorio real.

## 2. Actores y roles

| Actor | Quién es | Qué puede hacer |
|---|---|---|
| **Consultante** | Persona que necesita comprender una entidad, advertencia o fuente. | Buscar, leer fichas y explorar relaciones sin editar el catálogo |
| **Curador** | Persona que mantiene una distribución de Argos desde su checkout. | Editar el corpus, validarlo, revisar el cambio y publicarlo mediante Git |
| **Operador local** | Persona que arranca Argos desde el devcontainer. | Construir y proyectar automáticamente la versión incluida en su checkout |
| **Agentes de Argos** | Especialistas que investigan un caso. | Consultar la proyección activa mediante herramientas acotadas |
| **Repositorio federado** | Otro catálogo OKF publicado y fijado a una revisión. | Aportar un subgrafo explícitamente adoptado por el curador |

No existe autenticación de usuario en esta iniciativa: el entorno es local y la
autoridad para editar la concede el acceso al checkout. Publicar cambios sigue
dependiendo de los permisos del repositorio Git.

## 3. Objetivos y trabajos del usuario

**Objetivos del producto**

- Que el conocimiento usado por Argos sea legible, revisable y reproducible.
- Que la vista humana y la consulta de los agentes procedan del mismo contrato.
- Que un fallo al importar nunca deje una mezcla de dos versiones activa.
- Que el grafo explique relaciones y procedencia sin exponer datos privados.
- Que otro usuario pueda usar el catálogo incluido, fijar uno publicado o
  mantener una distribución propia.

**Trabajos del consultante**

- Encontrar una entidad por nombre o identificador.
- Entender qué advertencia la menciona, quién la emitió y qué fuente la sostiene.
- Cambiar entre vistas de riesgo, identidad, procedencia e historia.
- Abrir el vecindario de una ficha sin enfrentarse primero al grafo completo.

**Trabajos del curador**

- Añadir o corregir una ficha con relaciones tipadas y metadatos verificables.
- Detectar referencias rotas, tipos desconocidos y procedencia incompleta antes
  de aceptar el cambio.
- Ver en Git exactamente qué conocimiento cambia y conservar su historia.
- Adoptar un catálogo externo fijando una revisión, sin crear una dependencia
  remota durante el análisis.

## 4. Puntos de entrada y salida

| Punto | Actor | Dirección | Resultado |
|---|---|---|---|
| **Corpus del repositorio** | Curador | entra | Fichas Markdown con metadatos y relaciones tipadas |
| **Validar/construir catálogo** | Curador, sistema | entra/sale | Bundle OKF o errores concretos asociados a una ficha y regla |
| **Explorador local** | Consultante, curador | sale | Fichas, búsqueda, vecindario y modos del grafo |
| **Arranque del devcontainer** | Operador local | entra | Proyección activa desde el bundle validado incluido en el checkout |
| **Consulta de conocimiento** | Agentes | sale | Coincidencias y relaciones de la versión activa |
| **Git diff/PR** | Curador | entra/sale | Revisión y publicación del cambio editorial |
| **Repositorio federado** | Curador | entra | Subgrafo fijado a una revisión y materializado localmente |

## 5. Flujos

### W1 · Explorar el conocimiento

**Inicio**: el consultante o curador abre el explorador local o una ficha.

1. Busca por título, alias o identificador, o entra desde una ficha enlazada.
2. La ficha muestra descripción, estado, propiedades relevantes y procedencia.
3. Abre el grafo centrado en esa ficha y expande únicamente las relaciones que
   necesita.
4. Cambia de modo para responder una pregunta: riesgo, identidad, procedencia o
   historia.
5. Abre una ficha relacionada o vuelve al punto anterior sin perder el contexto.

**Fin**: comprende qué se sabe, cómo se relaciona y de dónde procede.

**Caminos alternativos**

- Una búsqueda sin resultados lo indica sin inventar coincidencias.
- Una relación no resuelta impide publicar el catálogo; no aparece como nodo
  genérico silencioso.

### W2 · Curar una ficha

**Inicio**: el curador crea o modifica una ficha en el checkout.

1. Usa un tipo y relaciones del vocabulario cerrado.
2. Declara los campos obligatorios y enlaza la procedencia cuando la afirmación
   lo exige.
3. Ejecuta la construcción y recibe un bundle actualizado o errores con
   fichero, regla y elemento.
4. Revisa el diff de Git, incluido el bundle derivado, las altas, retiradas y
   cambios de relaciones.
5. Acepta el cambio mediante el flujo Git habitual.

**Fin**: existe una revisión explícita y reproducible del catálogo.

**Caminos alternativos**

- Un error no produce bundle ni altera la proyección activa.
- Retirar una advertencia cambia su estado; no borra su ficha ni su historia.

### W3 · Proyectar el catálogo localmente

**Inicio**: se arranca el entorno o el operador solicita reconstruir el
conocimiento.

1. Se lee el bundle versionado incluido en el checkout, sin descargar ni
   construir herramientas.
2. Se valida su esquema, vocabulario, referencias, unicidad y procedencia.
3. Si ya está activa la misma versión y contenido, la operación termina sin
   reescribir.
4. Si es nueva, se prepara la proyección completa y se activa en una sola
   operación.
5. Los agentes consultan exclusivamente la versión activa.

**Fin**: la proyección identifica la revisión Git y el hash del bundle del que
procede.

**Caminos alternativos**

- Si construir, validar o escribir falla, la versión anterior continúa activa.
- El arranque no continúa con una proyección parcial ni vacía presentada como
  válida.

### W4 · Adoptar otro catálogo

**Inicio**: el curador decide incorporar conocimiento mantenido en otro
repositorio.

1. Declara el repositorio y una revisión inmutable.
2. La construcción obtiene y valida ese subgrafo.
3. El bundle resultante identifica su origen y evita colisiones de nombres.
4. La proyección local incorpora el subgrafo y el análisis deja de depender del
   repositorio remoto.

**Fin**: el checkout produce un catálogo compuesto y reproducible.

**Camino alternativo**: si la revisión no existe o el subgrafo no valida, se
conserva la última proyección válida.

## 6. Reglas y restricciones funcionales

- **R1 · Fuente canónica.** Las fichas versionadas en Git son la única fuente
  editorial. El bundle y la base local son derivados reconstruibles.
- **R2 · Vocabulario cerrado.** Todo nodo tiene un tipo conocido y toda relación
  una etiqueta conocida. Ampliarlos exige cambiar antes la especificación.
- **R3 · Identidad estable.** Cada ficha posee un identificador estable; moverla
  o renombrarla no puede fusionar silenciosamente dos conceptos.
- **R4 · Procedencia.** Toda advertencia enlaza exactamente una fuente pública,
  un regulador y la entidad advertida, y conserva fecha de captura y estado.
- **R5 · Validación total.** Tipos desconocidos, campos obligatorios ausentes,
  identificadores duplicados y relaciones no resueltas son errores bloqueantes.
- **R6 · Proyección atómica.** Solo existe una versión activa. Una importación
  fallida no modifica la versión anterior.
- **R7 · Idempotencia.** Reimportar el mismo hash no cambia filas ni revisiones.
- **R8 · Historia explícita.** Una advertencia retirada o sustituida se conserva
  y se relaciona con su sucesora cuando exista.
- **R9 · Presentación declarativa.** Colores, tamaños, filtros y modos expresan
  preguntas de usuario; no alteran los hechos ni el esquema operacional.
- **R10 · Grafo contextual.** La entrada normal es una búsqueda, ficha o
  vecindario. El grafo completo es una vista opcional, no la pantalla inicial.
- **R11 · Privacidad.** El corpus puede contener conocimiento regulatorio
  público, pero nunca avisos, documentos, señales ni identificadores privados
  de consultantes o tenants.
- **R12 · Ejecución local.** Arrancar y consultar conocimiento durante un
  análisis no requiere red, servicios externos ni construir el explorador.
- **R13 · Federación fijada.** Un subgrafo remoto siempre se fija a una revisión
  inmutable y queda materializado en el bundle local.
- **R14 · Curación humana.** La automatización puede proponer nodos y relaciones;
  incorporarlos al catálogo exige revisión explícita del curador.

## 7. Conceptos de datos

| Concepto | Qué percibe el usuario | Campos o estados significativos |
|---|---|---|
| **Ficha** | Página legible que representa un concepto | identificador, título, tipo, descripción, alias, propiedades |
| **Entidad** | Empresa, dominio u otro identificador investigable | clase, valor normalizado, fuerza |
| **Advertencia** | Publicación de un regulador sobre una entidad | identificador, estado vigente/retirada, captura |
| **Regulador** | Organismo que emite una advertencia | código, nombre, jurisdicción |
| **Fuente** | Página o documento público que sostiene un hecho | URL, clase, fecha de consulta |
| **Relación** | Vínculo tipado entre dos fichas | origen, etiqueta, destino, derivada o declarada |
| **Bundle** | Representación validada y transportable del catálogo | esquema, revisión Git, hash, nodos, relaciones |
| **Proyección activa** | Versión que consultan los agentes | revisión Git, hash, fecha, recuentos |
| **Subgrafo** | Catálogo externo incorporado explícitamente | origen, revisión, namespace |

## 8. Representación gráfica

```text
buscar → abrir ficha → ver vecindario → cambiar modo → abrir relación

Modos iniciales
  Riesgo       entidad ← advertencia → regulador
  Procedencia  advertencia → fuente
  Identidad    entidad ↔ identificador ↔ entidad
  Historia     advertencia → sustituye → advertencia
```

Una ficha mantiene el texto como superficie principal. El grafo aparece como
acceso contextual y permite expandir, filtrar y navegar con teclado o puntero.
El estado se comunica con texto además de color.

## 9. Restricciones y decisiones de alcance

- La primera entrega contiene únicamente conocimiento sintético.
- El explorador es de solo lectura; editar mediante formularios pertenece al
  futuro dashboard local.
- AG-UI, conversación y operación de casos no forman parte de esta iniciativa.
- La ingesta automática de CNMV, FCA o I-SCAN pertenece a la vertical de fuentes.
- Los casos pueden superponer temporalmente su grafo operacional en el futuro,
  pero nunca se convierten en fichas del corpus.
- La federación se define en el contrato, pero no se incorpora un repositorio
  externo hasta disponer de uno que deba mantenerse de forma independiente.

## 10. Preguntas abiertas y asunciones

**Asunciones de la primera entrega**

- **A1** · El corpus se escribe en español y los identificadores técnicos en
  inglés.
- **A2** · La ruta canónica es `knowledge/` y cada fichero Markdown representa
  una ficha.
- **A3** · El explorador local usa el mismo contrato OKF que los grafos de CERN
  y Singular, fijado a una revisión concreta del toolkit.
- **A4** · La proyección inicial conserva el contrato genérico completo y deriva
  además las advertencias que consume el agente de registros.
- **A5** · El acceso local al explorador se publica solo en loopback.

**Preguntas no bloqueantes**

- **Q1** · ¿Qué partes del catálogo se publicarán online cuando contenga fuentes
  reales?
- **Q2** · ¿Se mantendrá un único corpus o se separarán tipologías, reguladores y
  guías en repositorios federados?
- **Q3** · ¿Qué campos podrá editar el dashboard sin abrir directamente el
  Markdown?
