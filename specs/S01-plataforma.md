# S01 · Plataforma

Esta vertical verifica la base disponible hoy en el devcontenedor: SurrealDB 3
con su MCP embebido, LiteLLM, Langfuse y el contenedor de la app. Cubre la
constitución §2, §7, §11 y §12. El puerto de AgentOS está reservado, pero el
servidor, el clúster de agentes, NATS y RustFS pertenecen a S02 y no se presentan
como implementados por S01.

Hechos verificados en SurrealDB 3.2.4 que fijan estos casos: el endpoint `/mcp`
está activo en `surreal start` sin flags; un usuario de base de datos solo
entra con `Authorization: Bearer` y un JWT de `/signin` (Basic solo vale para
root); una petición cuyo `Host` no está en `SURREAL_MCP_ALLOWED_HOSTS` recibe
403.

La arquitectura aprobada que se construye sobre esta base está en
[`S02-agentos-workers.md`](S02-agentos-workers.md). S02 no se considera
implementada hasta que sus casos de aceptación tengan test y código.

## S01.1 El esquema se aplica de forma idempotente

- Dado una SurrealDB recién arrancada
- Cuando se ejecuta `bootstrap-db` dos veces seguidas y después se cambian las
  contraseñas de los usuarios de base de datos y se vuelve a ejecutar
- Entonces existen el namespace `agno` con la base `sessions`, el namespace
  `argos` con la base `ops`, el usuario `agent` en `argos/ops` y el usuario
  `runtime` en `agno/sessions`; `schema_version:current` tiene versión 1 y
  fecha de aplicación; la segunda ejecución no falla ni cambia la versión y
  solo las contraseñas nuevas permiten iniciar sesión

## S01.2 El usuario de los agentes entra por MCP con token

- Dado el usuario `agent` de `argos/ops`
- Cuando se firma en `/signin` con namespace y base y se abre una sesión MCP en
  `/mcp` con `Authorization: Bearer`
- Entonces la sesión se inicializa y `tools/list` incluye `query`, `select`,
  `create`, `relate`, `info` y `list`

## S01.3 El usuario de los agentes no sale de argos/ops

- Dado una sesión MCP del usuario `agent`
- Cuando ejecuta `USE NS agno DB sessions; INFO FOR DB;` y después
  `DEFINE USER intruder ON DATABASE PASSWORD 'x' ROLES OWNER;`
- Entonces ambas devuelven un error de permisos y no existe ningún usuario
  `intruder` en `argos/ops`

## S01.4 Sin credenciales no hay datos

- Dado una sesión MCP abierta sin cabecera `Authorization`
- Cuando ejecuta cualquier consulta
- Entonces el MCP responde con un error que dice que el acceso anónimo no está
  permitido y no devuelve datos

## S01.5 El MCP acepta el hostname del compose

- Dado el servicio `surrealdb` del compose
- Cuando la app llama a `http://surrealdb:8000/mcp` desde su contenedor
- Entonces recibe 200 y no 403 por `Host` no permitido

## S01.6 LiteLLM responde al modelo mock con coste y sin claves de proveedor

- Dado LiteLLM arrancado sin `ANTHROPIC_API_KEY` ni `OPENAI_API_KEY`
- Cuando se pide una completion al modelo `mock` con la master key
- Entonces responde el texto del mock y la cabecera `x-litellm-response-cost`
  es mayor que cero

## S01.7 Un agente mínimo deja traza en Langfuse

- Dado un agente de Agno con el modelo `mock` a través de LiteLLM y la
  instrumentación activa
- Cuando se ejecuta una vez dentro de una traza raíz con un identificador de
  usuario único
- Entonces Langfuse devuelve, en menos de 60 segundos, al menos una observación
  de esa traza, al menos una lleva ese identificador de usuario y ninguna lleva
  uno distinto

Langfuse v4 arranca en modo `events_only`: el endpoint `/api/public/traces` no
existe (404) y la lectura se hace por `/api/public/v2/observations` filtrando
por `traceId`.

## S01.8 Agno persiste sus sesiones en agno/sessions y nada en argos/ops

- Dado el mismo agente con almacenamiento en `agno/sessions` como usuario
  `runtime`
- Cuando se ejecuta una vez
- Entonces `agno/sessions` contiene la sesión de esa ejecución y `argos/ops` no
  tiene ninguna tabla cuyo nombre empiece por `agno_`

## S01.9 El anclaje exige una referencia estructural en cada test

- Dado un caso técnico y un fichero de tests
- Cuando `spec-check` examina las funciones cuyo nombre empieza por `test_`
- Entonces solo reconoce el identificador que abre la primera línea del docstring
  de cada función, rechaza referencias desconocidas y señala los tests sin referencia

## S01.10 Los servicios publicados por el devcontenedor solo escuchan en loopback

- Dado el compose de desarrollo con credenciales locales conocidas
- Cuando publica un puerto para acceder desde el host
- Entonces el puerto se vincula a `127.0.0.1` y no queda expuesto en todas las
  interfaces de red
