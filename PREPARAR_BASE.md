# Preparar la base para QueryLens

Esto lo hace una sola vez el administrador de la base que se va a monitorear. Es un script por motor: activa lo que QueryLens necesita y crea el usuario `querylens_monitor`. Lo que ya está activo no se toca, así que correrlo de nuevo no rompe nada. Otro script por motor lo deshace ([Quitar QueryLens de la base](#quitar-querylens-de-la-base)).

Después, con ese usuario, se registra la base en QueryLens con `querylens add` (ver [INSTALL_HEADLESS.md](INSTALL_HEADLESS.md)).

| | PostgreSQL | MySQL |
|---|---|---|
| Versión mínima | 15 (17 o superior para todas las detecciones; ver [Versiones soportadas](telemetry_pipeline/PIPELINE_FLOW.md#versiones-soportadas-de-las-bases-monitoreadas)) | 8.0.18 (8.0.22 o superior para los informes completos de abrazo mortal); 8.4 LTS también |
| Script | [`setup_target_postgres.sql`](querylens_database/setup_target_postgres.sql) | [`setup_target_mysql.sql`](querylens_database/setup_target_mysql.sql) |
| Para deshacerlo | [`teardown_target_postgres.sql`](querylens_database/teardown_target_postgres.sql) | [`teardown_target_mysql.sql`](querylens_database/teardown_target_mysql.sql) |
| Quién lo corre | Un superusuario (`postgres`) | Un administrador (`root`) |
| Reinicio | Una vez, si el script lo pide | Una vez, si el script lo pide |

No sirve en servicios administrados (RDS, Aurora, Cloud SQL, Azure): los scripts cambian la configuración del servidor y, en Postgres, leen archivos de log.

## PostgreSQL

1. Copiar el script al servidor y correrlo en la base a monitorear:
   ```bash
   psql -U postgres -d <base> -f setup_target_postgres.sql
   ```
2. Escribir la contraseña de `querylens_monitor` cuando la pida. Si el usuario ya existía, el script pregunta antes de cambiarla.
3. Leer la última línea:
   - `Listo: ...`: no hay más pasos.
   - `Falta un paso: reinicie Postgres ...`: reiniciar una vez (`sudo systemctl restart postgresql`). No hace falta volver a correr el script.
   - `Falta un paso: estos parametros vienen de la linea de comandos ...`: Postgres arranca con flags `-c` (systemd, Docker) que ganan sobre el script. Cambiar esos flags con los valores de la tabla de abajo y reiniciar.
4. Con varias bases en el mismo servidor, correr el script en cada una. Desde la segunda ya no pide reinicio.

Si QueryLens corre en el mismo servidor y Postgres está instalado fuera de Docker, el host a registrar es `host.docker.internal`. Hay que permitir esa conexión una vez:

- En `postgresql.conf`, `listen_addresses` debe incluir la IP del bridge de Docker (normalmente `172.17.0.1`).
- En `pg_hba.conf`, agregar:
  ```
  host  all  querylens_monitor  172.16.0.0/12  scram-sha-256
  ```

Después, `sudo systemctl reload postgresql`, o reiniciar si cambió `listen_addresses`.

<details>
<summary>Qué cambia el script en Postgres</summary>

| Ajuste | Valor | Para qué |
|---|---|---|
| `shared_preload_libraries` | agrega `pg_stat_statements` | Estadísticas de consultas (requiere reinicio) |
| `session_preload_libraries` | agrega `auto_explain` | Planes reales de las consultas lentas |
| `pg_stat_statements.max` | 10000 como mínimo | Que no se descarten consultas (requiere reinicio) |
| `pg_stat_statements.track` | `all` | Incluye sentencias dentro de funciones |
| `track_activity_query_size` | 4096 como mínimo | Texto completo de las consultas en curso (requiere reinicio) |
| `track_counts` | `on` | Uso de tablas e índices |
| `auto_explain.log_min_duration` / `log_format` / `log_analyze` | `50ms` / `json` / `off` | Registra el plan de lo que tarda 50 ms o más, sin volver a ejecutar nada |
| `logging_collector` | `on` | Log en archivos (requiere reinicio si estaba apagado) |
| `log_destination` | agrega `jsonlog` | Log JSON que QueryLens lee |
| `log_filename` / `log_truncate_on_rotation` / `log_rotation_age` | `postgresql-%a.log` / `on` / `1d` | Solo si el servidor no escribía archivos de log: un archivo por día de la semana, que se reutiliza |
| `log_error_verbosity` / `log_min_messages` | `default` / `warning` | Solo si estaban en `terse` o más alto que `error`: los abrazos mortales necesitan el detalle |
| `log_lock_waits` | `on` | La fila que espera cada sesión de un abrazo mortal cuando la aplicación manda los valores aparte (`$1`) |

Las listas (`*_preload_libraries`, `log_destination`) se completan; lo que ya tenían se conserva. Por base, el script crea la extensión `pg_stat_statements`, el esquema `querylens` con las funciones `log_files()` y `read_log()` (solo leen archivos `.json` del directorio de logs) y le da `CONNECT` a `querylens_monitor`. El rol tiene `pg_monitor`: lee estadísticas, no datos. En la tabla `querylens.setup_changes` queda anotado cada cambio con el valor de antes, para poder deshacerlo.

</details>

## MySQL

1. Copiar el script al servidor y correrlo con el nombre de la base de la aplicación:
   ```bash
   mysql -u root -p <base> < setup_target_mysql.sql
   ```
2. La primera vez, la salida muestra la contraseña generada para `querylens_monitor` (columna `generated password`). Anotarla: es la que se usa en `querylens add`. Las siguientes corridas no la cambian. Para generar otra:
   ```sql
   ALTER USER 'querylens_monitor'@'%' IDENTIFIED BY RANDOM PASSWORD;
   ```
3. Leer la última línea:
   - `Listo: ...`: no hay más pasos.
   - `Falta un paso: reinicie MySQL ...`: reiniciar una vez (`sudo systemctl restart mysql`). No hace falta volver a correr el script.
4. Con varias bases en el mismo servidor, correr el script con cada una. Desde la segunda ya no pide reinicio.

Si MySQL corre en el mismo servidor que QueryLens y fuera de Docker, el host a registrar es `host.docker.internal`, y `bind-address` (en `my.cnf`) debe aceptar conexiones desde la red de Docker.

<details>
<summary>Qué cambia el script en MySQL</summary>

| Ajuste | Valor | Para qué |
|---|---|---|
| `performance_schema` | `ON` | Estadísticas de consultas (viene activo por defecto; requiere reinicio si estaba apagado) |
| `performance_schema_max_sql_text_length` | 4096 como mínimo | Texto completo de las consultas, para obtener su plan (requiere reinicio) |
| `innodb_print_all_deadlocks` / `log_error_verbosity` | `ON` / `3` | Cada abrazo mortal llega al log de errores |
| Consumers `statements_digest`, `events_statements_current`, `global_instrumentation`, `thread_instrumentation` e instrumentos `statement/%` | activos | Vienen activos por defecto; el script los reactiva si alguien los apagó |

La configuración queda con `SET PERSIST` en `mysqld-auto.cnf`, que gana sobre `my.cnf`. El valor de antes de cada variable queda anotado en la tabla `querylens.setup_changes` (una base `querylens` que solo usa el script), para poder deshacerlo. Los consumers no se persisten: si `my.cnf` los desactiva, hay que quitar esa línea. El usuario recibe `SELECT` sobre `performance_schema`, `PROCESS` (consultas en curso, bloqueos y abrazos mortales) y `SHOW VIEW` sobre la base, que le permite conectarse a ella y ver sus tablas e índices sin leer datos.

</details>

## Opcional: planes de ejecución (`EXPLAIN`)

Los scripts no dan acceso a los datos. Sin eso, QueryLens ve las consultas, sus tiempos, los bloqueos y los índices, pero no puede pedir el plan de las consultas lentas, y los antipatrones que dependen del plan no aparecen. Para `EXPLAIN`, el usuario necesita `SELECT` sobre las tablas, y con `SELECT` podría leer los datos. Es decisión del dueño de la base.

PostgreSQL, por schema:

```sql
GRANT USAGE ON SCHEMA <schema> TO querylens_monitor;
GRANT SELECT ON ALL TABLES IN SCHEMA <schema> TO querylens_monitor;
ALTER DEFAULT PRIVILEGES FOR ROLE <dueno> IN SCHEMA <schema>
    GRANT SELECT ON TABLES TO querylens_monitor;
```

MySQL:

```sql
GRANT SELECT ON `<base>`.* TO 'querylens_monitor'@'%';
```

## Comprobar

Después de registrar la base, `querylens health <id>` muestra lo que le falta a la base, con la solución. Sin `EXPLAIN` (la sección anterior) es normal ver `EXPLAIN_PERMISSION_DENIED`. Cualquier otro problema de configuración indica que falta un paso de esta guía.

## Quitar QueryLens de la base

Un script por motor deshace lo que hizo el de instalación: borra el usuario `querylens_monitor` con todos sus permisos (también los `SELECT` opcionales), quita `auto_explain` y el log JSON, y devuelve cada ajuste al valor que tenía antes de QueryLens. Lo que el servidor ya tenía queda igual, y un ajuste que alguien cambió después de instalar no se toca. Se puede correr más de una vez.

### PostgreSQL

1. Correrlo en cada base donde se instaló:
   ```bash
   psql -U postgres -d <base> -f teardown_target_postgres.sql
   ```
2. Leer el final:
   - `Falta un paso: corra este script tambien en estas bases ...`: QueryLens sigue instalado en esas bases, que siguen funcionando. El rol se borra al desinstalar la última.
   - `Reinicie Postgres para terminar ...`: el reinicio quita `pg_stat_statements` de la memoria y, si el script de instalación lo había encendido, apaga `logging_collector`. Hasta entonces nada se rompe.
   - `Listo: ...`: no hay más pasos.

La configuración del servidor se revierte al correr el script en la primera base donde se instaló, que es la que tiene el registro. Los archivos de log que ya se escribieron quedan en el directorio `log/` de Postgres; se pueden borrar a mano.

### MySQL

1. Correrlo una vez por servidor (no hace falta la base):
   ```bash
   mysql -u root -p < teardown_target_mysql.sql
   ```
2. Leer el final: `Listo: ...`. Si además dice que algunos valores vuelven a como estaban en el próximo reinicio, no es urgente: mientras tanto el servidor sigue con el texto de consultas ampliado.
