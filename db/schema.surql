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
DEFINE USER OVERWRITE agent ON DATABASE PASSWORD '{{AGENT_PASSWORD}}' ROLES EDITOR
    DURATION FOR TOKEN 12h, FOR SESSION 24h;
-- Identidad de desarrollo compartida por gateway, dispatcher, resumer, case
-- analyzer, worker y janitor. S02 §7 exige dividirla antes de producción.
DEFINE USER OVERWRITE ledger ON DATABASE PASSWORD '{{LEDGER_PASSWORD}}' ROLES EDITOR
    DURATION FOR TOKEN 12h, FOR SESSION 24h;

DEFINE TABLE IF NOT EXISTS schema_version SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS version ON schema_version TYPE int;
DEFINE FIELD IF NOT EXISTS applied_at ON schema_version TYPE datetime DEFAULT time::now();

-- Libro de trabajos y outbox (S02 §6). Toda fila de negocio lleva tenant_id y
-- revision para transiciones condicionales.

DEFINE TABLE IF NOT EXISTS tenant SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS name ON tenant TYPE string;
DEFINE FIELD IF NOT EXISTS active ON tenant TYPE bool;
DEFINE FIELD IF NOT EXISTS revision ON tenant TYPE int;

DEFINE TABLE IF NOT EXISTS case SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tenant_id ON case TYPE string;
DEFINE FIELD IF NOT EXISTS state ON case TYPE string;
DEFINE FIELD IF NOT EXISTS notice_hash ON case TYPE option<string>;
DEFINE FIELD IF NOT EXISTS language ON case TYPE option<string>;
DEFINE FIELD IF NOT EXISTS correlation_id ON case TYPE string;
DEFINE FIELD IF NOT EXISTS previous_case_id ON case TYPE option<string>;
DEFINE FIELD IF NOT EXISTS created_at ON case TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON case TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON case TYPE int;
DEFINE INDEX IF NOT EXISTS case_tenant_notice ON case FIELDS tenant_id, notice_hash, created_at;

DEFINE TABLE IF NOT EXISTS artifact SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tenant_id ON artifact TYPE string;
DEFINE FIELD IF NOT EXISTS case_id ON artifact TYPE string;
DEFINE FIELD IF NOT EXISTS bucket ON artifact TYPE string;
DEFINE FIELD IF NOT EXISTS key ON artifact TYPE string;
DEFINE FIELD IF NOT EXISTS state ON artifact TYPE string;
DEFINE FIELD IF NOT EXISTS sha256 ON artifact TYPE option<string>;
DEFINE FIELD IF NOT EXISTS size ON artifact TYPE int;
DEFINE FIELD IF NOT EXISTS mime ON artifact TYPE string;
DEFINE FIELD IF NOT EXISTS created_at ON artifact TYPE datetime;
DEFINE FIELD IF NOT EXISTS expires_at ON artifact TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON artifact TYPE int;

DEFINE TABLE IF NOT EXISTS document SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tenant_id ON document TYPE string;
DEFINE FIELD IF NOT EXISTS case_id ON document TYPE string;
DEFINE FIELD IF NOT EXISTS artifact_id ON document TYPE string;
DEFINE FIELD IF NOT EXISTS sha256 ON document TYPE string;
DEFINE FIELD IF NOT EXISTS mime ON document TYPE string;
DEFINE FIELD IF NOT EXISTS size ON document TYPE int;
DEFINE FIELD IF NOT EXISTS page_count ON document TYPE option<int>;
DEFINE FIELD IF NOT EXISTS state ON document TYPE string;
DEFINE FIELD IF NOT EXISTS created_at ON document TYPE datetime;
DEFINE FIELD IF NOT EXISTS expires_at ON document TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON document TYPE int;
DEFINE INDEX IF NOT EXISTS document_case_hash ON document FIELDS case_id, sha256 UNIQUE;

DEFINE TABLE IF NOT EXISTS job SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tenant_id ON job TYPE string;
DEFINE FIELD IF NOT EXISTS case_id ON job TYPE string;
DEFINE FIELD IF NOT EXISTS type ON job TYPE string;
DEFINE FIELD IF NOT EXISTS document_id ON job TYPE option<string>;
DEFINE FIELD IF NOT EXISTS state ON job TYPE string;
DEFINE FIELD IF NOT EXISTS attempt ON job TYPE int;
DEFINE FIELD IF NOT EXISTS max_attempts ON job TYPE int;
DEFINE FIELD IF NOT EXISTS lease_until ON job TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS extractor_version ON job TYPE string;
DEFINE FIELD IF NOT EXISTS options ON job TYPE string;
DEFINE FIELD IF NOT EXISTS public_error ON job TYPE option<string>;
DEFINE FIELD IF NOT EXISTS internal_error ON job TYPE option<string>;
DEFINE FIELD IF NOT EXISTS correlation_id ON job TYPE string;
DEFINE FIELD IF NOT EXISTS previous_job_id ON job TYPE option<string>;
DEFINE FIELD IF NOT EXISTS created_at ON job TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON job TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON job TYPE int;
DEFINE INDEX IF NOT EXISTS job_state_lease ON job FIELDS state, lease_until;
DEFINE INDEX IF NOT EXISTS job_case ON job FIELDS case_id;

DEFINE TABLE IF NOT EXISTS attempt SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tenant_id ON attempt TYPE string;
DEFINE FIELD IF NOT EXISTS job_id ON attempt TYPE string;
DEFINE FIELD IF NOT EXISTS number ON attempt TYPE int;
DEFINE FIELD IF NOT EXISTS consumer ON attempt TYPE string;
DEFINE FIELD IF NOT EXISTS state ON attempt TYPE string;
DEFINE FIELD IF NOT EXISTS started_at ON attempt TYPE datetime;
DEFINE FIELD IF NOT EXISTS finished_at ON attempt TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS lease_until ON attempt TYPE datetime;
DEFINE FIELD IF NOT EXISTS error_kind ON attempt TYPE option<string>;
DEFINE FIELD IF NOT EXISTS error_code ON attempt TYPE option<string>;
DEFINE FIELD IF NOT EXISTS revision ON attempt TYPE int;
DEFINE INDEX IF NOT EXISTS attempt_job ON attempt FIELDS job_id, number UNIQUE;

DEFINE TABLE IF NOT EXISTS outbox_entry SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tenant_id ON outbox_entry TYPE string;
DEFINE FIELD IF NOT EXISTS job_id ON outbox_entry TYPE string;
DEFINE FIELD IF NOT EXISTS kind ON outbox_entry TYPE string;
DEFINE FIELD IF NOT EXISTS subject ON outbox_entry TYPE string;
DEFINE FIELD IF NOT EXISTS message_id ON outbox_entry TYPE string;
DEFINE FIELD IF NOT EXISTS attempt ON outbox_entry TYPE int;
DEFINE FIELD IF NOT EXISTS state ON outbox_entry TYPE string;
DEFINE FIELD IF NOT EXISTS not_before ON outbox_entry TYPE datetime;
DEFINE FIELD IF NOT EXISTS lease_until ON outbox_entry TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS published_at ON outbox_entry TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS created_at ON outbox_entry TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON outbox_entry TYPE int;
DEFINE INDEX IF NOT EXISTS outbox_message ON outbox_entry FIELDS message_id UNIQUE;
DEFINE INDEX IF NOT EXISTS outbox_state ON outbox_entry FIELDS state, not_before;
DEFINE INDEX IF NOT EXISTS outbox_job ON outbox_entry FIELDS job_id;

DEFINE TABLE IF NOT EXISTS extraction SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tenant_id ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS case_id ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS document_id ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS extractor_version ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS options ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS state ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS sha256 ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS page_count ON extraction TYPE int;
DEFINE FIELD IF NOT EXISTS ocr_pages ON extraction TYPE int;
DEFINE FIELD IF NOT EXISTS text_artifact_id ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS manifest_artifact_id ON extraction TYPE string;
DEFINE FIELD IF NOT EXISTS created_at ON extraction TYPE datetime;
DEFINE FIELD IF NOT EXISTS expires_at ON extraction TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON extraction TYPE int;
DEFINE INDEX IF NOT EXISTS extraction_document ON extraction FIELDS document_id, extractor_version, options UNIQUE;

DEFINE TABLE IF NOT EXISTS chunk SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tenant_id ON chunk TYPE string;
DEFINE FIELD IF NOT EXISTS extraction_id ON chunk TYPE string;
DEFINE FIELD IF NOT EXISTS page ON chunk TYPE int;
DEFINE FIELD IF NOT EXISTS position ON chunk TYPE int;
DEFINE FIELD IF NOT EXISTS text ON chunk TYPE string;
DEFINE FIELD IF NOT EXISTS sha256 ON chunk TYPE string;
DEFINE FIELD IF NOT EXISTS expires_at ON chunk TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON chunk TYPE int;
DEFINE INDEX IF NOT EXISTS chunk_extraction ON chunk FIELDS extraction_id, position UNIQUE;

UPSERT schema_version:current SET version = 2;
