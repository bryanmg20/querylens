-- Crear el rol de sistema que espera pg_partman
CREATE ROLE postgres WITH SUPERUSER LOGIN;

CREATE EXTENSION IF NOT EXISTS pgmq;

SELECT pgmq.create('analyze_job');