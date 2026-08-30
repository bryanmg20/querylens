-- ============================================================
-- QUERYLENS — MySQL initialization script
-- ============================================================

-- -----------------------------------------------
-- app_user: NO CREAR - ya lo crea MySQL automáticamente
-- Solo dar permisos adicionales si son necesarios
-- -----------------------------------------------
GRANT PROCESS ON *.* TO 'app_user'@'%';
GRANT REPLICATION CLIENT ON *.* TO 'app_user'@'%';

-- -----------------------------------------------
-- querylens_monitor: QUERYLENS monitoring user (read-only)
-- -----------------------------------------------
CREATE USER IF NOT EXISTS 'querylens_monitor'@'%'
    IDENTIFIED BY 'monitor_pass';

-- Permisos para ql_demo (lectura)
GRANT SELECT ON ql_demo.* TO 'querylens_monitor'@'%';

-- Permisos para performance_schema (necesarios para tus queries)
GRANT SELECT ON performance_schema.* TO 'querylens_monitor'@'%';

-- ⚠️ NO GRANT EN information_schema - NO ES NECESARIO
-- Los permisos de información de esquemas se heredan automáticamente

-- Permiso PROCESS necesario para ver conexiones activas y locks
GRANT PROCESS ON *.* TO 'querylens_monitor'@'%';

-- Permiso REPLICATION CLIENT necesario para ver información de transacciones
GRANT REPLICATION CLIENT ON *.* TO 'querylens_monitor'@'%';

-- Aplicar cambios
FLUSH PRIVILEGES;

-- Verificar usuarios
SELECT user, host, plugin FROM mysql.user WHERE user IN ('app_user', 'querylens_monitor');

-- Verificar permisos
SHOW GRANTS FOR 'app_user'@'%';
SHOW GRANTS FOR 'querylens_monitor'@'%';