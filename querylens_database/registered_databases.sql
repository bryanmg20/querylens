-- Tabla de bases de datos registradas por el Auth Service.
-- Las credenciales nunca se guardan en texto plano: la contraseña se cifra
-- en la aplicacion antes de llegar aqui.
CREATE TABLE IF NOT EXISTS registered_databases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    database_identifier VARCHAR(32) UNIQUE NOT NULL,
    -- Hash determinístico de engine+host+port+db_user+database_name (ver
    -- app/security.py::compute_connection_fingerprint). El UNIQUE evita
    -- registrar la misma conexión más de una vez, aunque host/port/db_user
    -- se guarden cifrados (y por lo tanto no se pueda comparar UNIQUE sobre
    -- esas columnas directamente).
    connection_fingerprint VARCHAR(64) UNIQUE NOT NULL,
    connection_name VARCHAR(255) NOT NULL,
    engine VARCHAR(50) NOT NULL DEFAULT 'postgresql',
    -- host, port y db_user se guardan cifrados (Fernet); por eso son TEXT.
    host TEXT NOT NULL,
    port TEXT NOT NULL,
    db_user TEXT NOT NULL,
    encrypted_password TEXT NOT NULL,
    database_name VARCHAR(255) NOT NULL,
    -- Indica si la base de datos esta activa (TRUE) o no (FALSE). Es el
    -- registro de seguimiento de su estado.
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    -- Voluntad del usuario: FALSE cuando pauso el monitoreo desde la pagina de
    -- diagnosticos. A diferencia de is_active (que lo actualiza solo el chequeo
    -- de disponibilidad), esta columna solo la cambia el usuario; el pipeline
    -- extrae unicamente las bases con is_active Y monitoring_enabled.
    monitoring_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    -- Ventana de recoleccion programada desde el CLI (cli/querylens.sh). NULL
    -- en las dos = monitoreo continuo, sin ventana ni reporte automatico. El
    -- servicio scheduler enciende monitoring_enabled al llegar collect_from
    -- (y anota collect_started_at), lo apaga al llegar collect_until y, tras
    -- un margen, escribe el reporte en reports/ y anota report_written_at.
    collect_from TIMESTAMPTZ,
    collect_until TIMESTAMPTZ,
    collect_started_at TIMESTAMPTZ,
    report_written_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Volumenes creados antes de la ventana: el initdb no vuelve a correr, asi que
-- db-roles (service_roles.sql) aplica este archivo en cada up.
ALTER TABLE registered_databases
    ADD COLUMN IF NOT EXISTS collect_from TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS collect_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS collect_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS report_written_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_registered_databases_identifier
    ON registered_databases (database_identifier);
