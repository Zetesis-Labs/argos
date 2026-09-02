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

DEFINE TABLE IF NOT EXISTS schema_version SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS version ON schema_version TYPE int;
DEFINE FIELD IF NOT EXISTS applied_at ON schema_version TYPE datetime DEFAULT time::now();
UPSERT schema_version:current SET version = 1;
