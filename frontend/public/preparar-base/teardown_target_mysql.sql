-- Quita de un MySQL lo que puso setup_target_mysql.sql: el usuario
-- querylens_monitor (con todos sus permisos, en todas las bases), la base
-- querylens (el registro del setup) y la configuracion que el setup cambio,
-- que vuelve a como estaba antes de QueryLens. El paso a paso esta en
-- PREPARAR_BASE.md.
--
--   mysql -u root -p < teardown_target_mysql.sql
--
-- Una vez por servidor, con un usuario administrador (root). Es idempotente:
-- correrlo de nuevo no rompe nada.
--
-- Los consumers e instrumentos de performance_schema no se tocan: el setup
-- solo reactiva los que vienen activos por defecto.

-- Sin registro (setup nunca corrido o ya revertido) el procedimiento no
-- encuentra filas y solo se borra el usuario.
CREATE DATABASE IF NOT EXISTS querylens;
CREATE TABLE IF NOT EXISTS querylens.setup_changes (
    name VARCHAR(64) PRIMARY KEY,
    previous_global TEXT,
    previous_persisted TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Lo que el setup dejo para el proximo reinicio y todavia no rige: se
-- revierte en mysqld-auto.cnf, pero el valor actual sigue hasta reiniciar.
SET @ql_restart = EXISTS (
    SELECT 1
    FROM querylens.setup_changes c
    JOIN performance_schema.global_variables g ON g.VARIABLE_NAME = c.name
    WHERE c.name IN ('performance_schema', 'performance_schema_max_sql_text_length',
                     'performance_schema_events_statements_history_size')
      AND c.previous_global <> g.VARIABLE_VALUE);

-- Por cada variable registrada: las dinamicas vuelven ya a su valor de antes
-- (SET GLOBAL); en mysqld-auto.cnf, RESET PERSIST si no estaba persistida o
-- el valor persistido de antes si lo estaba. Los nombres van en SQL dinamico
-- (PREPARE), que solo admite un procedimiento.
DROP PROCEDURE IF EXISTS querylens.ql_restore;
DELIMITER //
CREATE PROCEDURE querylens.ql_restore()
BEGIN
    DECLARE done INT DEFAULT 0;
    DECLARE v_name VARCHAR(64);
    DECLARE v_global, v_persisted TEXT;
    DECLARE cur CURSOR FOR
        SELECT name, previous_global, previous_persisted FROM querylens.setup_changes;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;

    OPEN cur;
    restore_loop: LOOP
        FETCH cur INTO v_name, v_global, v_persisted;
        IF done THEN
            LEAVE restore_loop;
        END IF;

        IF v_name IN ('innodb_print_all_deadlocks', 'log_error_verbosity') THEN
            SET @ql_stmt = CONCAT('SET GLOBAL ', v_name, ' = ',
                                  IF(v_global REGEXP '^-?[0-9]+$', v_global, QUOTE(v_global)));
            PREPARE ql_stmt FROM @ql_stmt;
            EXECUTE ql_stmt;
            DEALLOCATE PREPARE ql_stmt;
        END IF;

        IF v_persisted IS NULL THEN
            SET @ql_stmt = CONCAT('RESET PERSIST IF EXISTS ', v_name);
        ELSE
            SET @ql_stmt = CONCAT(IF(v_name IN ('innodb_print_all_deadlocks', 'log_error_verbosity'),
                                     'SET PERSIST ', 'SET PERSIST_ONLY '),
                                  v_name, ' = ',
                                  IF(v_persisted REGEXP '^-?[0-9]+$', v_persisted, QUOTE(v_persisted)));
        END IF;
        PREPARE ql_stmt FROM @ql_stmt;
        EXECUTE ql_stmt;
        DEALLOCATE PREPARE ql_stmt;
    END LOOP;
    CLOSE cur;
END//
DELIMITER ;

CALL querylens.ql_restore();

-- El usuario y sus permisos (SELECT sobre performance_schema, PROCESS,
-- SHOW VIEW y los SELECT opcionales sobre cada base).
DROP USER IF EXISTS 'querylens_monitor'@'%';
DROP DATABASE querylens;

SELECT IF(@ql_restart,
          'Listo. Algunos valores (performance_schema_max_sql_text_length, performance_schema_events_statements_history_size) vuelven a como estaban en el proximo reinicio de MySQL; no hace falta reiniciar ahora.',
          'Listo: QueryLens ya no tiene nada en este servidor.') AS querylens;
