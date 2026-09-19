-- ============================================================
-- QUERYLENS — MySQL initialization script
-- ============================================================

-- -----------------------------------------------
-- app_user: NO CREAR - ya lo crea MySQL automáticamente
-- -----------------------------------------------
GRANT PROCESS ON *.* TO 'app_user'@'%';
GRANT REPLICATION CLIENT ON *.* TO 'app_user'@'%';

-- -----------------------------------------------
-- querylens_monitor: QUERYLENS monitoring user (read-only)
-- -----------------------------------------------
CREATE USER IF NOT EXISTS 'querylens_monitor'@'%'
    IDENTIFIED BY 'monitor_pass';

GRANT SELECT ON *.* TO 'querylens_monitor'@'%';
GRANT PROCESS ON *.* TO 'querylens_monitor'@'%';
GRANT REPLICATION CLIENT ON *.* TO 'querylens_monitor'@'%';

-- ⚠️ NO GRANT EN information_schema - NO ES NECESARIO
-- Los permisos de información de esquemas se heredan automáticamente

FLUSH PRIVILEGES;

-- Verificar usuarios
SELECT user, host, plugin FROM mysql.user WHERE user IN ('app_user', 'querylens_monitor');

-- Verificar permisos
SHOW GRANTS FOR 'app_user'@'%';
SHOW GRANTS FOR 'querylens_monitor'@'%';