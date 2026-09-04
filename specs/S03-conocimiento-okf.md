# S03 · Fundación de conocimiento OKF

**Estado**: en implementación.

Esta vertical convierte las decisiones de `argos/conocimiento` en una base
ejecutable: corpus OKF en Git, explorador local y proyección atómica en
SurrealDB. Cubre conocimiento/W1–W3 y R1–R12, R14. La federación R13 queda
preparada por el contrato fijado, pero sin un subgrafo real en esta entrega.

## 1. Objetivo y límites

S03 debe proporcionar:

- un corpus `knowledge/` exclusivamente sintético y legible como Markdown;
- un perfil OKF propio con tipos, propiedades, relaciones y modos cerrados;
- una construcción reproducible con `quartz-okf` fijado a un commit;
- un explorador de solo lectura accesible desde el devcontainer en loopback;
- un bundle `okf-graph/v1` versionado como única frontera de importación;
- una proyección completa y atómica en `argos/ops` identificada por commit y
  hash de contenido;
- la derivación de las advertencias que ya consulta `registries_agent`.

No implementa edición visual, AG-UI, descarga de fuentes, datos regulatorios
reales ni grafos de casos privados.

## 2. Flujo de datos

```text
knowledge/*.md + okf.config.mjs
              │
              ▼
     quartz-okf fijado por SHA
              │ valida y construye
              ▼
 knowledge/dist/okf-graph.json
       │                    │
       ▼                    ▼
 explorador opcional  importador Argos
                            │ transacción
                            ▼
      knowledge_snapshot + knowledge_node + knowledge_edge + warning
```

El runtime no interpreta Markdown ni mantiene un segundo vocabulario. Solo
acepta el documento `okf-graph/v1` producido por el toolkit fijado. El perfil
OKF declara el vocabulario completo; Python valida que nodos y relaciones
pertenezcan al vocabulario declarado y solo conoce los cuatro tipos y tres
relaciones que transforma en advertencias operacionales.

## 3. Contrato del corpus

Tipos iniciales: `entity`, `warning`, `regulator`, `source`, `jurisdiction`,
`typology`, `pattern` y `guidance`.

Relaciones iniciales: `Warns about`, `Issued by`, `Cites`, `Operates in`,
`Same as`, `Supersedes`, `Part of` y `Contains`. Las inversas de composición se
derivan; el resto se declara una sola vez.

Propiedades mínimas:

- todos los tipos: `knowledge_id`, una URI estable y única independiente de la
  ruta del fichero;
- `entity`: `entity_kind`, `entity_value`, `strength`;
- `warning`: `warning_id`, `status`, `captured_at`;
- `regulator`: `code`;
- `source`: `url`, `source_kind`.

Cada advertencia declara exactamente un destino para `Warns about`, `Issued by`
y `Cites`. `status` admite `active` y `withdrawn`; retirar nunca elimina.

## 4. Proyección local

El importador valida por completo antes de abrir la transacción. Calcula SHA-256
sobre los bytes del bundle y compara el hash y la versión del proyector con
`knowledge_snapshot:current`. Si coinciden, devuelve sin escribir. Si cambia
el contenido o la transformación:

1. reemplaza `knowledge_node` y `knowledge_edge` por el grafo completo;
2. deriva `warning` desde nodos y relaciones, normalizando su entidad con las
   reglas existentes;
3. escribe `knowledge_snapshot:current` con esquema, revisión Git, hash, fecha y
   recuentos;
4. confirma todo junto mediante una identidad de bootstrap, no la de otro
   workload.

Una excepción cancela la transacción y conserva íntegra la versión anterior.
Los identificadores de fila se derivan de `knowledge_id` y de las relaciones
entre esos identificadores mediante hash. Los slugs y rutas originales se
conservan para navegación y trazabilidad, pero renombrar o mover una ficha no
cambia su identidad.

## 5. Desarrollo local

El bundle validado se versiona junto al corpus y permite arrancar sin red, npm ni
Node. `bootstrap-local` lo lee directamente del checkout y no depende del sitio
de documentación.

El servicio opcional `knowledge`, bajo el perfil `docs`, usa una imagen oficial
de Node 22 y una caché propia para reconstruir y servir el explorador sin
introducir Node en el runtime Python. Publica su puerto únicamente en
`127.0.0.1`. Los tests puros reciben bytes de bundle sin red; los casos de
integración usan SurrealDB de test.

## 6. Privacidad

`knowledge/` no contiene datos procedentes de consultas, documentos ni casos.
Las advertencias de demostración usan dominios reservados `.example`. A partir
de la futura vertical de fuentes podrá contener advertencias regulatorias
públicas aceptadas por el curador, nunca entradas privadas del consultante.

## 7. Casos anclados

## S03.1 El corpus y el perfil producen un grafo OKF cerrado

- Dado el corpus sintético de Argos y el toolkit fijado
- Cuando se construye y valida el sitio
- Entonces `okf-graph/v1` contiene solo los tipos y relaciones declarados,
  identifica la revisión fuente, no tiene relaciones sin resolver y cada ficha
  aparece una sola vez con una URI `knowledge_id` estable y única
  (conocimiento/W2; conocimiento/R1–R5)

## S03.2 El explorador expresa preguntas sin cambiar los hechos

- Dado el perfil visual de Argos
- Cuando el consultante abre una ficha o el explorador
- Entonces dispone de acceso contextual y modos de riesgo, identidad,
  procedencia e historia; estado y tipo tienen etiquetas legibles y el puerto
  local solo escucha en loopback (conocimiento/W1; conocimiento/R9–R10)

## S03.3 Un bundle incompleto nunca llega a la proyección

- Dado un documento que no es `okf-graph/v1`, omite o duplica un
  `knowledge_id`, duplica un slug, deja una relación sin destino o presenta una
  advertencia sin entidad, regulador, fuente, estado o captura únicos
- Cuando el importador lo valida
- Entonces informa del elemento y la regla incumplida y no llama al puerto de
  proyección (conocimiento/W2–W3; conocimiento/R2–R5)

## S03.4 La proyección activa es completa, trazable e idempotente

- Dado un bundle válido con entidades, reguladores, fuentes y advertencias
  sintéticas
- Cuando se importa dos veces
- Entonces la primera operación activa en una transacción todos sus nodos,
  relaciones y advertencias derivadas junto con revisión Git, hash y versión
  del proyector; la segunda no escribe, y la consulta de registros devuelve la
  advertencia vigente normalizada (conocimiento/W3; conocimiento/R6–R8)

## S03.5 Un fallo conserva la versión activa anterior

- Dado una proyección válida activa en SurrealDB y otra versión cuya escritura
  viola una restricción a mitad de la transacción
- Cuando se intenta activar la segunda
- Entonces la versión, nodos, relaciones y advertencias anteriores continúan
  observables sin mezcla con la nueva (conocimiento/W3; conocimiento/R6)

## S03.6 El devcontainer construye, sirve y proyecta el catálogo al arrancar

- Dado un checkout sin cachés ni base local y sin acceso a una fuente remota de
  conocimiento
- Cuando el operador arranca el perfil `services`
- Entonces `bootstrap-local` activa el bundle versionado antes de iniciar los
  procesos de Argos; y, si arranca aparte el perfil `docs`, el servicio
  `knowledge` reconstruye y sirve el explorador en un puerto de loopback sin
  bloquear al producto (conocimiento/W3; conocimiento/R1, R6, R12)

## S03.7 El corpus separa conocimiento público de datos privados

- Dado todos los ficheros del corpus y su configuración
- Cuando se auditan antes de publicar
- Entonces solo contienen ejemplos sintéticos bajo dominios reservados, no
  incluyen casos, tenants, documentos ni identificadores privados, y no existe
  un segundo catálogo operativo en fixtures o JSON manual (conocimiento/R11,
  R14; constitución §6, §13)
