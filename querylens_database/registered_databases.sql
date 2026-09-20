-- Tabla de bases de datos registradas por el Auth Service.
-- Las credenciales nunca se guardan en texto plano: la contraseña se cifra
-- en la aplicacion antes de llegar aqui.
CREATE TABLE IF NOT EXISTS registered_databases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    database_identifier VARCHAR(32) UNIQUE NOT NULL,
    connection_name VARCHAR(255) NOT NULL,
    engine VARCHAR(50) NOT NULL DEFAULT 'postgresql',
    -- host, port y db_user se guardan cifrados (Fernet); por eso son TEXT.
    host TEXT NOT NULL,
    port TEXT NOT NULL,
    db_user TEXT NOT NULL,
    encrypted_password TEXT NOT NULL,
    database_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_registered_databases_identifier
    ON registered_databases (database_identifier);
