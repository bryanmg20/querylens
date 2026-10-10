-- Salud del pipeline de extraccion, por base registrada.
--
-- Lo escribe solo telemetry_pipeline (health/writer.py); la API REST lo lee
-- por db_id para avisarle al cliente que la extraccion falla o sale incompleta
-- (extension faltante, permisos, conexion). Los textos de message y
-- remediation ya vienen redactados desde health/catalog.py: la API no
-- necesita conocer los codigos.
--
-- En un volumen ya inicializado el initdb no vuelve a correr: aplicar a mano
--   psql -U $QUERYLENS_USER -d $QUERYLENS_DB -f querylens_database/pipeline_health.sql
CREATE SCHEMA IF NOT EXISTS pipeline_health;

-- Una fila por base: resultado del ultimo ciclo. Se pisa en cada ciclo.
CREATE TABLE IF NOT EXISTS pipeline_health.last_run (
    db_id           TEXT PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('ok', 'degraded', 'failed')),
    sections_ok     TEXT[] NOT NULL DEFAULT '{}',
    sections_failed TEXT[] NOT NULL DEFAULT '{}',
    -- Snapshot encolado en este ciclo; NULL si no se encolo nada.
    msg_id          BIGINT,
    engine_version  TEXT
);

-- Un problema abierto por (db_id, code, section). El pipeline hace upsert en
-- cada ciclo (last_seen/occurrences), lo marca resuelto cuando un ciclo que lo
-- volvio a evaluar ya no lo ve, y la purga borra los resueltos tras 15 dias.
CREATE TABLE IF NOT EXISTS pipeline_health.issues (
    id          BIGSERIAL PRIMARY KEY,
    db_id       TEXT NOT NULL,
    code        TEXT NOT NULL,
    -- connection | credentials | collect | preflight | explain | pipeline
    scope       TEXT NOT NULL,
    -- connection | extension | permission | config | version | internal
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('blocking', 'degraded', 'info')),
    -- '' y no NULL: el indice unico parcial trataria cada NULL como distinto.
    section     TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL,
    remediation TEXT NOT NULL,
    -- Solo codigos (sqlstate/errno), contadores y valores de settings: nunca
    -- texto de queries, mensajes crudos del driver ni credenciales.
    params      JSONB NOT NULL DEFAULT '{}',
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurrences INT NOT NULL DEFAULT 1,
    resolved_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_issues_open
    ON pipeline_health.issues (db_id, code, section)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_issues_db_seen
    ON pipeline_health.issues (db_id, last_seen DESC);

CREATE INDEX IF NOT EXISTS ix_issues_resolved
    ON pipeline_health.issues (resolved_at)
    WHERE resolved_at IS NOT NULL;
