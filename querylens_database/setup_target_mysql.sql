-- Prepara un MySQL para que QueryLens lo monitoree (la base del cliente, no
-- la de QueryLens). Un solo script con todo lo que necesita el pipeline; el
-- paso a paso esta en PREPARAR_BASE.md.
--
--   mysql -u root -p <base> < setup_target_mysql.sql
--
-- Con un usuario administrador (root), una vez por cada base a monitorear
-- (<base>, la de la aplicacion): el usuario y la configuracion del servidor
-- se aplican la primera vez, y en las demas bases solo el permiso sobre la
-- base. Es idempotente: lo que ya esta bien no cambia, y correrlo de nuevo no
-- rompe nada.
--
-- Al final:
--   * la primera vez muestra la contrasena generada para querylens_monitor
--     (la que se ingresa en querylens add). Las siguientes no la cambia;
--   * dice si hace falta reiniciar MySQL. Solo lo pide si cambio algo que
--     MySQL lee al arrancar (performance_schema, el largo del texto de las
--     consultas y el tamano del historial de sentencias).
--
-- Requiere MySQL 8.0.18 o superior (MariaDB no esta soportado); antes de la
-- 8.0.22 no existe performance_schema.error_log y los informes de abrazo
-- mortal llegan incompletos. La configuracion queda con SET PERSIST en
-- mysqld-auto.cnf, que MySQL lee despues de my.cnf y de la linea de
-- comandos: gana sobre ambos.

-- ---------------------------------------------------------------------------
-- 0. Registro de la configuracion antes de QueryLens, para que
--    teardown_target_mysql.sql la devuelva tal cual (y no a los valores de
--    fabrica). Una fila por variable; si el script se repite, INSERT IGNORE
--    conserva la primera.
-- ---------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS querylens;
CREATE TABLE IF NOT EXISTS querylens.setup_changes (
    name VARCHAR(64) PRIMARY KEY,
    -- valor vigente antes del setup
    previous_global TEXT,
    -- valor en mysqld-auto.cnf antes del setup (NULL = no estaba)
    previous_persisted TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT IGNORE INTO querylens.setup_changes (name, previous_global, previous_persisted)
SELECT g.VARIABLE_NAME, g.VARIABLE_VALUE, p.VARIABLE_VALUE
FROM performance_schema.global_variables g
LEFT JOIN performance_schema.persisted_variables p ON p.VARIABLE_NAME = g.VARIABLE_NAME
WHERE g.VARIABLE_NAME IN ('performance_schema', 'performance_schema_max_sql_text_length',
                          'performance_schema_events_statements_history_size',
                          'innodb_print_all_deadlocks', 'log_error_verbosity');

-- ---------------------------------------------------------------------------
-- 1. Usuario de monitoreo: lectura de performance_schema y PROCESS (consultas
--    en ejecucion, transacciones, bloqueos e informes de abrazo mortal). No
--    lee datos de las tablas.
-- ---------------------------------------------------------------------------
-- Si el usuario ya existe no se toca su contrasena (IF NOT EXISTS solo deja
-- un warning). Para generar otra:
--   ALTER USER 'querylens_monitor'@'%' IDENTIFIED BY RANDOM PASSWORD;
CREATE USER IF NOT EXISTS 'querylens_monitor'@'%' IDENTIFIED BY RANDOM PASSWORD;
GRANT SELECT ON performance_schema.* TO 'querylens_monitor'@'%';
GRANT PROCESS ON *.* TO 'querylens_monitor'@'%';

-- SHOW VIEW sobre la base de la aplicacion: sin ningun permiso sobre ella el
-- usuario no puede conectarse a esa base (QueryLens la prueba al registrarla)
-- y information_schema no le muestra sus tablas ni sus indices. SHOW VIEW no
-- lee datos. GRANT no admite un nombre variable: se arma con PREPARE.
SET @ql_db = DATABASE();
SET @ql_grant = IF(@ql_db IS NULL, 'DO 0',
                   CONCAT('GRANT SHOW VIEW ON `', REPLACE(@ql_db, '`', '``'),
                          '`.* TO ''querylens_monitor''@''%'''));
PREPARE ql_grant FROM @ql_grant;
EXECUTE ql_grant;
DEALLOCATE PREPARE ql_grant;

-- EXPLAIN y las columnas de las tablas necesitan SELECT, y con SELECT el
-- usuario podria leer los datos. Es decision del dueno de la base; sin esto
-- QueryLens ve las consultas y los indices, pero no los planes. Para darlo:
--
--   GRANT SELECT ON `<base>`.* TO 'querylens_monitor'@'%';

-- ---------------------------------------------------------------------------
-- 2. Configuracion que MySQL lee al arrancar (PERSIST_ONLY: se aplica en el
--    proximo reinicio).
-- ---------------------------------------------------------------------------
-- Estadisticas de consultas (ON por defecto en MySQL 8).
SET PERSIST_ONLY performance_schema = ON;
-- Texto de las consultas: con 1024 bytes (el valor por defecto) las largas
-- llegan truncadas y no se puede obtener su plan. Solo se sube.
SET @ql_sql_text = GREATEST(@@GLOBAL.performance_schema_max_sql_text_length, 4096);
SET PERSIST_ONLY performance_schema_max_sql_text_length = @ql_sql_text;
-- Historial de sentencias por conexion (events_statements_history): QueryLens
-- explica una ejecucion que siga ahi y compara su estimacion con las filas que
-- devolvio (AP-08). Con 10 (el valor por defecto) las consultas lentas y poco
-- frecuentes casi nunca siguen ahi cuando la conexion ejecuta muchas otras; con
-- 100 se encontraron entre el 50 y el 84 % de las veces
-- (detection_engine/references/004_ap08_filas_misma_ejecucion.md). Solo se sube.
SET @ql_history = GREATEST(@@GLOBAL.performance_schema_events_statements_history_size, 100);
SET PERSIST_ONLY performance_schema_events_statements_history_size = @ql_history;

-- ---------------------------------------------------------------------------
-- 3. Configuracion dinamica (PERSIST: se aplica ya y queda para los
--    reinicios).
-- ---------------------------------------------------------------------------
-- Cada abrazo mortal llega a performance_schema.error_log: InnoDB lo escribe
-- como Note, que la verbosidad 2 descarta.
SET PERSIST innodb_print_all_deadlocks = ON;
SET PERSIST log_error_verbosity = 3;

-- Consumers e instrumentos de sentencias. Vienen activos por defecto; esto
-- los reactiva si alguien los apago en caliente. No se persisten (no son
-- variables): si my.cnf los desactiva, hay que quitarlo de alli.
UPDATE performance_schema.setup_consumers
SET ENABLED = 'YES'
WHERE NAME IN ('global_instrumentation', 'thread_instrumentation',
               'statements_digest', 'events_statements_current',
               'events_statements_history')
  AND ENABLED <> 'YES';
UPDATE performance_schema.setup_instruments
SET ENABLED = 'YES', TIMED = 'YES'
WHERE NAME LIKE 'statement/%' AND (ENABLED <> 'YES' OR TIMED <> 'YES');

-- ---------------------------------------------------------------------------
-- 4. Resultado.
-- ---------------------------------------------------------------------------
-- Lo persistido que todavia no rige (de arranque) pide reinicio. Sin
-- persisted_globals_load MySQL ignora mysqld-auto.cnf al arrancar.
SELECT CASE
           WHEN @ql_db IS NULL THEN
               'Falta un paso: corra el script con el nombre de la base de la aplicacion: mysql -u root -p <base> < setup_target_mysql.sql'
           WHEN @@GLOBAL.persisted_globals_load = 0 THEN
               'Falta un paso: persisted_globals_load esta en OFF y MySQL ignora lo que configuro este script. Ver PREPARAR_BASE.md.'
           WHEN EXISTS (SELECT 1
                        FROM performance_schema.persisted_variables p
                        JOIN performance_schema.global_variables g USING (VARIABLE_NAME)
                        WHERE p.VARIABLE_NAME IN ('performance_schema',
                                                  'performance_schema_max_sql_text_length',
                                                  'performance_schema_events_statements_history_size')
                          AND p.VARIABLE_VALUE <> g.VARIABLE_VALUE) THEN
               'Falta un paso: reinicie MySQL para terminar, por ejemplo: sudo systemctl restart mysql. Despues no hace falta volver a correr este script.'
           ELSE
               'Listo: el servidor ya tiene todo lo que necesita QueryLens.'
       END AS querylens;
