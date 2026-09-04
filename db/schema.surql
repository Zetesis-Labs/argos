-- Esquema de Argos. Idempotente: se aplica en cada arranque (constitución §7).
-- Las contraseñas llegan sustituidas por bootstrap-db desde el entorno.

DEFINE NAMESPACE IF NOT EXISTS agno;
USE NS agno;
DEFINE DATABASE IF NOT EXISTS sessions;
USE DB sessions;
DEFINE USER OVERWRITE runtime ON DATABASE PASSWORD '{{RUNTIME_PASSWORD}}' ROLES OWNER
    DURATION FOR TOKEN 12h, FOR SESSION 24h;

DEFINE NAMESPACE IF NOT EXISTS argos;
USE NS argos;
DEFINE DATABASE IF NOT EXISTS ops;
USE DB ops;
-- Los agentes solo leen: sus escrituras entran por los casos de uso con la
-- identidad del workload (S02 §7).
DEFINE USER OVERWRITE agent ON DATABASE PASSWORD '{{AGENT_PASSWORD}}' ROLES VIEWER
    DURATION FOR TOKEN 12h, FOR SESSION 24h;
-- La identidad compartida de desarrollo desaparece: cada workload tiene la suya
-- y rotar o revocar una no afecta a las demás.
REMOVE USER IF EXISTS ledger ON DATABASE;
-- El pipeline asíncrono se retiró: sus identidades desaparecen con él.
REMOVE USER IF EXISTS dispatcher ON DATABASE;
REMOVE USER IF EXISTS resumer ON DATABASE;
REMOVE USER IF EXISTS analyzer ON DATABASE;
REMOVE USER IF EXISTS worker ON DATABASE;
REMOVE USER IF EXISTS janitor ON DATABASE;
DEFINE USER OVERWRITE gateway ON DATABASE PASSWORD '{{GATEWAY_PASSWORD}}' ROLES EDITOR
    DURATION FOR TOKEN 12h, FOR SESSION 24h;

DEFINE TABLE IF NOT EXISTS schema_version SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS version ON schema_version TYPE int;
DEFINE FIELD IF NOT EXISTS applied_at ON schema_version TYPE datetime DEFAULT time::now();

-- Casos y memoria. Argos es local y de un solo usuario: no hay tenant, no hay
-- credenciales de cliente y una fila no pertenece a nadie más que a su caso.

REMOVE TABLE IF EXISTS artifact;
REMOVE TABLE IF EXISTS document;
REMOVE TABLE IF EXISTS job;
REMOVE TABLE IF EXISTS attempt;
REMOVE TABLE IF EXISTS outbox_entry;
REMOVE TABLE IF EXISTS extraction;
REMOVE TABLE IF EXISTS chunk;
REMOVE TABLE IF EXISTS tenant;

-- Un DEFINE TABLE OVERWRITE no retira las definiciones de campo: hay que
-- quitarlas una a una para que el esquema quede en su forma nueva.
REMOVE INDEX IF EXISTS case_tenant_notice ON case;
REMOVE FIELD IF EXISTS tenant_id ON case;
REMOVE FIELD IF EXISTS notice_hash ON case;
REMOVE FIELD IF EXISTS correlation_id ON case;
REMOVE FIELD IF EXISTS previous_case_id ON case;
REMOVE FIELD IF EXISTS reviewed_by ON case;
REMOVE FIELD IF EXISTS public_error ON case;
REMOVE FIELD IF EXISTS expires_at ON case;
REMOVE FIELD IF EXISTS tenant_id ON case_entity;
REMOVE FIELD IF EXISTS tenant_id ON signal;
REMOVE FIELD IF EXISTS tenant_id ON verdict;

DEFINE TABLE IF NOT EXISTS case SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS state ON case TYPE string;
DEFINE FIELD IF NOT EXISTS notice_text ON case TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS notice_links ON case TYPE array<string> DEFAULT [];
DEFINE FIELD IF NOT EXISTS language ON case TYPE option<string>;
DEFINE FIELD IF NOT EXISTS review_state ON case TYPE string DEFAULT 'unreviewed';
DEFINE FIELD IF NOT EXISTS reviewed_at ON case TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS error ON case TYPE option<string>;
DEFINE FIELD IF NOT EXISTS created_at ON case TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON case TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON case TYPE int;

-- Memoria entre casos (R29): la entidad y sus vínculos sobreviven al caso que
-- los descubrió, y el vínculo caso-entidad es lo que permite ver reincidencias.

DEFINE TABLE IF NOT EXISTS entity SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS kind ON entity TYPE string;
DEFINE FIELD IF NOT EXISTS value ON entity TYPE string;
DEFINE FIELD IF NOT EXISTS strength ON entity TYPE string;
DEFINE FIELD IF NOT EXISTS first_seen_at ON entity TYPE datetime;
DEFINE FIELD IF NOT EXISTS last_seen_at ON entity TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON entity TYPE int;
DEFINE INDEX IF NOT EXISTS entity_value ON entity FIELDS kind, value UNIQUE;

DEFINE TABLE IF NOT EXISTS entity_link SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS left_entity_id ON entity_link TYPE string;
DEFINE FIELD IF NOT EXISTS right_entity_id ON entity_link TYPE string;
DEFINE FIELD IF NOT EXISTS reason ON entity_link TYPE string;
DEFINE FIELD IF NOT EXISTS created_at ON entity_link TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON entity_link TYPE int;
DEFINE INDEX IF NOT EXISTS entity_link_pair ON entity_link FIELDS left_entity_id, right_entity_id UNIQUE;

DEFINE TABLE IF NOT EXISTS case_entity SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS case_id ON case_entity TYPE string;
DEFINE FIELD IF NOT EXISTS entity_id ON case_entity TYPE string;
DEFINE FIELD IF NOT EXISTS created_at ON case_entity TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON case_entity TYPE int;
DEFINE INDEX IF NOT EXISTS case_entity_pair ON case_entity FIELDS case_id, entity_id UNIQUE;
DEFINE INDEX IF NOT EXISTS case_entity_entity ON case_entity FIELDS entity_id;

DEFINE TABLE IF NOT EXISTS warning SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS regulator ON warning TYPE string;
DEFINE FIELD IF NOT EXISTS url ON warning TYPE string;
DEFINE FIELD IF NOT EXISTS entity_kind ON warning TYPE string;
DEFINE FIELD IF NOT EXISTS entity_value ON warning TYPE string;
DEFINE FIELD IF NOT EXISTS active ON warning TYPE bool;
DEFINE FIELD IF NOT EXISTS captured_at ON warning TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON warning TYPE int;
DEFINE INDEX IF NOT EXISTS warning_entity ON warning FIELDS entity_kind, entity_value;

-- Proyección reconstruible del bundle OKF. Las propiedades permanecen como JSON
-- canónico; los campos consultados por negocio se derivan a tablas tipadas.
DEFINE TABLE IF NOT EXISTS knowledge_snapshot SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS source_head ON knowledge_snapshot TYPE string;
DEFINE FIELD IF NOT EXISTS content_hash ON knowledge_snapshot TYPE string;
DEFINE FIELD IF NOT EXISTS graph_schema ON knowledge_snapshot TYPE string;
DEFINE FIELD IF NOT EXISTS profile ON knowledge_snapshot TYPE string;
DEFINE FIELD IF NOT EXISTS projection_version ON knowledge_snapshot TYPE int;
DEFINE FIELD IF NOT EXISTS imported_at ON knowledge_snapshot TYPE datetime;
DEFINE FIELD IF NOT EXISTS node_count ON knowledge_snapshot TYPE int;
DEFINE FIELD IF NOT EXISTS edge_count ON knowledge_snapshot TYPE int;
DEFINE FIELD IF NOT EXISTS warning_count ON knowledge_snapshot TYPE int;

DEFINE TABLE IF NOT EXISTS knowledge_node SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS knowledge_id ON knowledge_node TYPE string;
DEFINE FIELD IF NOT EXISTS slug ON knowledge_node TYPE string;
DEFINE FIELD IF NOT EXISTS title ON knowledge_node TYPE string;
DEFINE FIELD IF NOT EXISTS node_type ON knowledge_node TYPE string;
DEFINE FIELD IF NOT EXISTS description ON knowledge_node TYPE option<string>;
DEFINE FIELD IF NOT EXISTS path ON knowledge_node TYPE option<string>;
DEFINE FIELD IF NOT EXISTS properties_json ON knowledge_node TYPE string;
DEFINE FIELD IF NOT EXISTS source_head ON knowledge_node TYPE string;
DEFINE INDEX IF NOT EXISTS knowledge_node_identity ON knowledge_node FIELDS knowledge_id UNIQUE;
DEFINE INDEX IF NOT EXISTS knowledge_node_slug ON knowledge_node FIELDS slug UNIQUE;

DEFINE TABLE IF NOT EXISTS knowledge_edge SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS source_knowledge_id ON knowledge_edge TYPE string;
DEFINE FIELD IF NOT EXISTS target_knowledge_id ON knowledge_edge TYPE string;
DEFINE FIELD IF NOT EXISTS source_slug ON knowledge_edge TYPE string;
DEFINE FIELD IF NOT EXISTS target_slug ON knowledge_edge TYPE string;
DEFINE FIELD IF NOT EXISTS label ON knowledge_edge TYPE string;
DEFINE FIELD IF NOT EXISTS derived ON knowledge_edge TYPE bool;
DEFINE FIELD IF NOT EXISTS source_head ON knowledge_edge TYPE string;
DEFINE INDEX IF NOT EXISTS knowledge_edge_identity ON knowledge_edge FIELDS source_knowledge_id, label, target_knowledge_id UNIQUE;

DEFINE TABLE IF NOT EXISTS signal SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS case_id ON signal TYPE string;
DEFINE FIELD IF NOT EXISTS analysis ON signal TYPE string;
DEFINE FIELD IF NOT EXISTS code ON signal TYPE string;
DEFINE FIELD IF NOT EXISTS strength ON signal TYPE string;
DEFINE FIELD IF NOT EXISTS official ON signal TYPE bool;
DEFINE FIELD IF NOT EXISTS recidivism ON signal TYPE bool;
DEFINE FIELD IF NOT EXISTS source ON signal TYPE string;
DEFINE FIELD IF NOT EXISTS observed_at ON signal TYPE datetime;
DEFINE FIELD IF NOT EXISTS value ON signal TYPE string;
DEFINE FIELD IF NOT EXISTS quote ON signal TYPE string;
DEFINE FIELD IF NOT EXISTS created_at ON signal TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON signal TYPE int;
DEFINE INDEX IF NOT EXISTS signal_case ON signal FIELDS case_id;

DEFINE TABLE IF NOT EXISTS verdict SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS case_id ON verdict TYPE string;
DEFINE FIELD IF NOT EXISTS version ON verdict TYPE int;
DEFINE FIELD IF NOT EXISTS level ON verdict TYPE string;
DEFINE FIELD IF NOT EXISTS outcome ON verdict TYPE string;
DEFINE FIELD IF NOT EXISTS state ON verdict TYPE string;
DEFINE FIELD IF NOT EXISTS language ON verdict TYPE string;
DEFINE FIELD IF NOT EXISTS summary ON verdict TYPE string;
DEFINE FIELD IF NOT EXISTS actions ON verdict TYPE array<string>;
DEFINE FIELD IF NOT EXISTS missing ON verdict TYPE array<string>;
DEFINE FIELD IF NOT EXISTS created_at ON verdict TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON verdict TYPE int;
DEFINE INDEX IF NOT EXISTS verdict_case_version ON verdict FIELDS case_id, version UNIQUE;
DEFINE INDEX IF NOT EXISTS verdict_case_state ON verdict FIELDS case_id, state;

UPSERT schema_version:current SET version = 7;
