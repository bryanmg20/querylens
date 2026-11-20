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
    -- Voluntad del usuario: una base nueva nace en FALSE y registrarla de nuevo
    -- no cambia el valor. Pasa a TRUE cuando el usuario inicia el monitoreo desde
    -- la pagina de diagnosticos o el CLI, o programa una ventana. A diferencia de
    -- is_active (que lo actualiza solo el chequeo de disponibilidad), no depende
    -- de si la base responde; el pipeline extrae unicamente las bases con
    -- is_active Y monitoring_enabled.
    monitoring_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    -- Ventana de recoleccion programada desde el CLI (cli/querylens.sh). NULL
    -- en las dos = sin ventana ni reporte automatico: el monitoreo sigue como lo
    -- deje el usuario. El
    -- servicio scheduler enciende monitoring_enabled al llegar collect_from
    -- (y anota collect_started_at), lo apaga al llegar collect_until y, tras
    -- un margen, escribe el reporte en reports/ y anota report_written_at.
    collect_from TIMESTAMPTZ,
    collect_until TIMESTAMPTZ,
    collect_started_at TIMESTAMPTZ,
    report_written_at TIMESTAMPTZ,
    -- Inicio de la sesion de deteccion: la pagina de diagnosticos y el reporte
    -- con --session cuentan como "esta sesion" solo las detecciones
    -- posteriores; las anteriores quedan en el historico (no se borra nada).
    -- Solo cambia cuando el monitoreo arranca (con el monitoreo encendido el
    -- detector todavia procesa snapshots de antes del corte): al iniciar por
    -- primera vez, al reanudar con sesion nueva (pagina de diagnosticos,
    -- querylens resume --new-session) y cuando el scheduler abre una ventana
    -- con el monitoreo apagado.
    detection_session_started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
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

-- Volumenes creados antes de la sesion de deteccion: las bases ya registradas
-- empiezan su primera sesion al aplicar esto (now() se evalua una vez).
ALTER TABLE registered_databases
    ADD COLUMN IF NOT EXISTS detection_session_started_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Volumenes creados cuando el monitoreo nacia encendido. Solo cambia el valor
-- por defecto: las bases ya registradas siguen como estan.
ALTER TABLE registered_databases
    ALTER COLUMN monitoring_enabled SET DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_registered_databases_identifier
    ON registered_databases (database_identifier);
