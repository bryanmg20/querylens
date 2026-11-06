# Instalación de auto_explain para QueryLens (Postgres self-hosted)

Esta guía activa los **planes reales** en Postgres. Sin ella, QueryLens explica las consultas con `EXPLAIN (GENERIC_PLAN)`, que estima sin conocer los valores. Con ella, Postgres escribe en su log el plan con el que **ejecutó** cada consulta lenta, y QueryLens lo lee por la misma conexión SQL de siempre.

Funciona con cualquier driver de la aplicación (JDBC, Go, Node, .NET, Rails, Python…), porque el plan lo escribe Postgres al ejecutar.

> **Para los clientes:** [`querylens_database/setup_target_postgres.sql`](../../querylens_database/setup_target_postgres.sql) aplica esta guía completa (Pasos 2 y 4, junto con `pg_stat_statements` y el rol) en una sola corrida, y al final dice si hace falta el reinicio del Paso 3. Ver [PREPARAR_BASE.md](../../PREPARAR_BASE.md). Esta guía queda como referencia de qué cambia y por qué.

> **Sandbox del repo:** `ql_sandbox/docker-compose.yml` ya trae todo esto. Los `-c` del servicio `postgres` aplican la configuración de los Pasos 2 y 3, y el servicio `postgres_setup` ejecuta `ql_sandbox/init/auto_explain_postgres.sql` (el SQL del Paso 4) en cada `docker compose up`, también con un volumen que ya existía. Esta guía es para las bases de los clientes.

## Requisitos

| Requisito | Por qué |
|-----------|---------|
| PostgreSQL 15 o superior | El formato `jsonlog` existe desde la 15. Con 17 o superior el pipeline tiene todas las detecciones (`PIPELINE_FLOW.md`, "Versiones soportadas") |
| Postgres **self-hosted** (servidor propio, VM o contenedor) | El script crea funciones que leen archivos del servidor y necesita un **superusuario real**. RDS, Aurora, Cloud SQL y Azure no lo permiten |
| Acceso como superusuario (por ejemplo `postgres`) | Solo para ejecutar el script una vez |
| Poder reiniciar Postgres una vez | Solo si `logging_collector` está en `off` (es el caso del sandbox) |
| Rol de monitoreo ya creado (`querylens_monitor` en el sandbox) | Es el rol que recibe permiso para leer el log |

## Qué cambia en el servidor

| Cambio | Efecto | Aplica con |
|--------|--------|-----------|
| `session_preload_libraries = 'auto_explain'` | Carga `auto_explain` en cada conexión nueva | Reload (conexiones nuevas) |
| `auto_explain.log_min_duration = '50ms'` | Solo se registran las consultas que tardan 50 ms o más de ejecución | Reload |
| `auto_explain.log_analyze = on` | El plan registrado trae, además de las filas **estimadas**, las filas **reales** de esa ejecución (AP-08 compara las dos). No vuelve a ejecutar nada: Postgres cuenta las filas mientras ejecuta las consultas de la aplicación. Ese conteo corre en todas las consultas, no solo en las que se registran | Reload |
| `auto_explain.log_timing = off` | Con `log_analyze`, no mide el tiempo de cada paso del plan, que es la parte cara. Con esto el costo no se distinguió del ruido en el sandbox (−2 % a +3 %; `detection_engine/references/004_ap08_filas_misma_ejecucion.md`) | Reload |
| `auto_explain.log_format = json` | Plan en JSON: el mismo formato que ya procesa QueryLens | Reload |
| `logging_collector = on` | Postgres escribe el log en archivos dentro del servidor | **Reinicio** |
| `log_destination = 'stderr,jsonlog'` | Además del log de texto, un archivo `.json` con una línea JSON por evento (incluye `query_id`) | Reload |
| `log_filename = 'postgresql-%a.log'` | Un archivo por día de la semana (`postgresql-Mon.json`, `postgresql-Tue.json`…) | Reload |
| `log_truncate_on_rotation = on` | Al cambiar de día, el archivo de ese día se vacía y se reutiliza: el directorio nunca pasa de 7 archivos por formato | Reload |
| `log_rotation_age = '1d'` | La rotación es diaria (es el valor por defecto; se fija para que la limpieza no dependa de él) | Reload |
| Esquema `querylens` con 2 funciones | `querylens.log_files()` lista los `.json` del directorio de logs; `querylens.read_log()` lee un tramo de uno de ellos | Inmediato |

El rol de monitoreo **no recibe escritura ni superusuario**: solo `EXECUTE` sobre esas 2 funciones.

## Paso 1 — Revisar la configuración actual

Conéctate como superusuario. En el sandbox:

```bash
docker exec -it ql_postgres psql -U ql_user -d ql_demo
```

(`ql_user` es el superusuario del sandbox: es el `POSTGRES_USER` de la imagen.)

```sql
SHOW session_preload_libraries;   -- si ya tiene valores, hay que conservarlos (ver Paso 2)
SHOW logging_collector;           -- off = hará falta reiniciar en el Paso 3
SHOW log_destination;
SHOW log_directory;               -- 'log' = relativo al directorio de datos
SHOW compute_query_id;            -- auto basta si pg_stat_statements está cargado
SHOW log_min_messages;            -- error o más bajo (default: warning): los abrazos mortales son errores
SHOW log_error_verbosity;         -- default o verbose; terse quita el DETAIL que trae el ciclo
SHOW log_lock_waits;              -- on: la fila que espera cada sesión de un abrazo (default: off)
```

Las mismas funciones del Paso 4 le sirven a QueryLens para leer los informes de abrazo mortal (`deadlock detected`) que Postgres escribe en el log. Con `log_min_messages` y `log_error_verbosity` en sus valores por defecto no hay nada que cambiar.

El informe no dice qué fila esperaba cada sesión; QueryLens la saca de los valores escritos en la sentencia (`WHERE id = 1`). Si la aplicación los manda aparte (`WHERE id = $1`: psycopg 3, JDBC, node-postgres, la mayoría de los ORM), la saca del aviso `still waiting` que Postgres escribe con `log_lock_waits = on` cuando una sesión espera más que `deadlock_timeout`. Sin ese aviso el abrazo se registra igual, pero sin distinguir CT-01 de CT-02. Es dinámico:

```sql
ALTER SYSTEM SET log_lock_waits = on;
SELECT pg_reload_conf();
```

Escribe una línea por cada espera de bloqueo que pasa de `deadlock_timeout` (1 s por defecto), no solo de los abrazos.

## Paso 2 — Activar auto_explain y el log JSON

```sql
-- Registra los parámetros auto_explain.* en esta sesión; sin esto
-- ALTER SYSTEM no los reconoce.
LOAD 'auto_explain';

-- Si el Paso 1 mostró valores en session_preload_libraries, agrégalos aquí
-- separados por coma (por ejemplo 'otra_lib,auto_explain'): este comando
-- reemplaza el valor, no lo suma.
ALTER SYSTEM SET session_preload_libraries = 'auto_explain';

ALTER SYSTEM SET auto_explain.log_min_duration = '50ms';
ALTER SYSTEM SET auto_explain.log_analyze = on;
ALTER SYSTEM SET auto_explain.log_timing = off;
ALTER SYSTEM SET auto_explain.log_format = 'json';

ALTER SYSTEM SET logging_collector = on;
ALTER SYSTEM SET log_destination = 'stderr,jsonlog';

-- Rotación con limpieza automática: un archivo por día de la semana que se
-- vacía al reutilizarse. Postgres nunca borra logs; con nombres que se
-- repiten, el directorio queda en 7 archivos .json y 7 .log.
ALTER SYSTEM SET log_filename = 'postgresql-%a.log';
ALTER SYSTEM SET log_truncate_on_rotation = on;
ALTER SYSTEM SET log_rotation_age = '1d';

SELECT pg_reload_conf();
```

`ALTER SYSTEM` guarda los valores en `postgresql.auto.conf`, dentro del directorio de datos. En el sandbox ese directorio es el volumen `postgres_data`, así que sobreviven a reinicios del contenedor.

Ninguno de estos parámetros está en el `command:` de `ql_sandbox/docker-compose.yml`. Si lo estuvieran, el flag `-c` del arranque ganaría sobre `ALTER SYSTEM`.

## Paso 3 — Reiniciar (solo si `logging_collector` estaba en `off`)

```bash
docker restart ql_postgres
```

En un servidor propio sería `sudo systemctl restart postgresql`, o el equivalente de tu instalación.

Después del reinicio, `docker logs ql_postgres` solo muestra el arranque: el resto del log pasa a los archivos de `log/` dentro del directorio de datos.

## Paso 4 — Crear las funciones de lectura

Conéctate de nuevo como superusuario y ejecuta:

```sql
CREATE SCHEMA IF NOT EXISTS querylens;
REVOKE ALL ON SCHEMA querylens FROM PUBLIC;

-- Lista los archivos .json del directorio de logs (nombre, tamaño, última modificación).
CREATE OR REPLACE FUNCTION querylens.log_files()
RETURNS TABLE (name text, size bigint, modification timestamptz)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT name, size, modification
    FROM pg_ls_logdir()
    WHERE name LIKE '%.json'
$$;

-- Devuelve hasta max_bytes del archivo indicado, desde start_offset.
-- Solo acepta un nombre de archivo .json, sin rutas: no puede salir del
-- directorio de logs. Devuelve bytea, no text: un corte en medio de un
-- caracter UTF-8 no rompe la lectura (QueryLens decodifica las líneas).
CREATE OR REPLACE FUNCTION querylens.read_log(file text, start_offset bigint, max_bytes bigint)
RETURNS bytea
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF file !~ '^[A-Za-z0-9_.-]+\.json$' OR position('..' IN file) > 0 THEN
        RAISE EXCEPTION 'querylens.read_log: archivo no permitido';
    END IF;
    IF start_offset < 0 OR max_bytes <= 0 OR max_bytes > 16777216 THEN
        RAISE EXCEPTION 'querylens.read_log: rango no permitido';
    END IF;
    RETURN pg_read_binary_file(
        current_setting('log_directory') || '/' || file, start_offset, max_bytes, true
    );
END
$$;

-- Las funciones nacen ejecutables por PUBLIC: se quita y se da solo al monitor.
REVOKE ALL ON FUNCTION querylens.log_files() FROM PUBLIC;
REVOKE ALL ON FUNCTION querylens.read_log(text, bigint, bigint) FROM PUBLIC;

GRANT USAGE ON SCHEMA querylens TO querylens_monitor;
GRANT EXECUTE ON FUNCTION querylens.log_files() TO querylens_monitor;
GRANT EXECUTE ON FUNCTION querylens.read_log(text, bigint, bigint) TO querylens_monitor;
```

Si tu rol de monitoreo tiene otro nombre, reemplaza `querylens_monitor` en las 3 últimas líneas.

### Qué hace cada pieza

- **`SECURITY DEFINER`**: la función corre con los permisos de quien la creó (el superusuario), no con los de quien la llama. Por eso el monitor puede leer el log sin tener permisos sobre archivos.
- **`SET search_path = pg_catalog`**: evita que alguien cree un objeto con el mismo nombre en otro esquema y lo cuele en la función. Es la práctica recomendada para toda función `SECURITY DEFINER`.
- **La validación del nombre** (`^[A-Za-z0-9_.-]+\.json$`, sin `..`): sin barras no hay rutas, así que solo se pueden leer archivos `.json` del directorio de logs.
- **El tope de 16 MB por llamada** acota cuánto se puede leer de una vez.
- **`pg_read_binary_file(..., true)`**: el `true` hace que devuelva `NULL` si el archivo ya no existe (por ejemplo, porque rotó entre listar y leer), en vez de fallar.

## Paso 5 — Verificar

**Como superusuario**, confirma que `auto_explain` quedó cargado en las conexiones nuevas:

```sql
SHOW auto_explain.log_min_duration;   -- 50ms
SHOW auto_explain.log_analyze;        -- on
SHOW auto_explain.log_timing;         -- off
SHOW logging_collector;               -- on
SHOW log_destination;                 -- stderr,jsonlog
SHOW log_filename;                    -- postgresql-%a.log
SHOW log_truncate_on_rotation;        -- on
```

Genera una consulta lenta para que aparezca en el log. Tiene que pasar de 50 ms **de ejecución**: `auto_explain` no cuenta el envío ni el parseo. En el sandbox esta tarda ~650 ms:

```sql
SELECT count(*) FROM sbtest1 a JOIN sbtest1 b ON b.k = a.k JOIN sbtest1 c ON c.k = b.k WHERE a.id < 2000;
```

**Como el rol de monitoreo** (en el sandbox: `docker exec -it ql_postgres psql -h localhost -U querylens_monitor -d ql_demo`, password `monitor_pass`):

```sql
-- Debe listar al menos un archivo .json (postgresql-<día>.json).
SELECT * FROM querylens.log_files() ORDER BY modification DESC;

-- Los últimos 4 KB del archivo más reciente, como texto. Debe aparecer
-- una línea con "duration: ... plan:" y el JSON del plan.
SELECT convert_from(
    querylens.read_log(f.name, greatest(f.size - 4096, 0), 4096), 'UTF8'
)
FROM querylens.log_files() f
ORDER BY f.modification DESC
LIMIT 1;

-- Debe fallar con "archivo no permitido": no se puede salir del directorio.
SELECT querylens.read_log('../postgresql.auto.conf', 0, 100);
```

Si la conversión falla por un carácter cortado al principio del tramo, prueba con otro offset: QueryLens maneja ese caso, la consulta de verificación no.

## Consideraciones

- **Literales en el log.** El log guarda las consultas lentas con sus valores reales: en el texto (`Query Text`), en `Query Parameters` (los valores de `$1`, `$2` cuando la app usa parámetros) y en los filtros del plan. Sigue en el disco de tu servidor y bajo tu control. QueryLens lo lee y redacta los literales a `$n` antes de enviar nada a la cola; nunca guarda el texto crudo.
- **Umbral.** Con 50 ms se registran las consultas medianas y lentas. Las muy rápidas y frecuentes (5 ms × miles de llamadas) no pasan el umbral y QueryLens las explica con el plan genérico. Para cubrirlas, baja el umbral a `0` y muestrea: `auto_explain.sample_rate = 0.05` registra el 5 % de las ejecuciones, elegidas al azar. Sin `sample_rate`, un umbral de `0` escribe un plan por cada consulta.
- **Tamaño y limpieza del log.** Con `postgresql-%a.log` y `log_truncate_on_rotation = on` se guarda una semana de historia y el archivo del día se vacía al reutilizarse 7 días después. Un día de mucho tráfico puede pasar de `log_rotation_size` (10 MB): la rotación por tamaño reabre el mismo nombre y sigue agregando, y la truncación solo ocurre en la rotación por tiempo. QueryLens lee cada pocos segundos, así que no necesita más historia.
- **Archivos con el formato anterior.** Si el log ya estaba activo con el nombre por defecto (`postgresql-2026-10-11_214142.json`), esos archivos no se borran solos: elimínalos a mano una vez desde el directorio `log/` del servidor.
- **Pools de conexiones.** `session_preload_libraries` aplica a las conexiones **nuevas**. Una aplicación con pool empieza a registrar planes cuando el pool recicla sus conexiones (o tras reiniciar la aplicación).
- **Nuestras propias consultas.** `auto_explain` también registraría las consultas de QueryLens si fueran lentas. El pipeline descarta las del rol de monitoreo.

## Si ya lo instalaste con la versión anterior de esta guía

La primera versión usaba un umbral de 500 ms y no reciclaba los logs. Como superusuario:

```sql
ALTER SYSTEM SET auto_explain.log_min_duration = '50ms';
ALTER SYSTEM SET log_filename = 'postgresql-%a.log';
ALTER SYSTEM SET log_truncate_on_rotation = on;
ALTER SYSTEM SET log_rotation_age = '1d';
SELECT pg_reload_conf();
```

No hace falta reiniciar. Postgres empieza a escribir en `postgresql-<día>.json` en la siguiente rotación (al cambiar de día). Para que empiece ya, fuerza una rotación:

```sql
SELECT pg_rotate_logfile();
```

Después borra los archivos con el nombre anterior. En el sandbox:

```bash
docker exec ql_postgres sh -c 'rm /var/lib/postgresql/data/log/postgresql-20*'
```

## Desinstalar

```sql
DROP SCHEMA querylens CASCADE;
ALTER SYSTEM RESET session_preload_libraries;   -- o vuelve a poner el valor que tenía
ALTER SYSTEM RESET auto_explain.log_min_duration;
ALTER SYSTEM RESET auto_explain.log_analyze;
ALTER SYSTEM RESET auto_explain.log_timing;
ALTER SYSTEM RESET auto_explain.log_format;
ALTER SYSTEM RESET log_destination;
ALTER SYSTEM RESET log_filename;
ALTER SYSTEM RESET log_truncate_on_rotation;
ALTER SYSTEM RESET log_rotation_age;
ALTER SYSTEM RESET logging_collector;            -- requiere reinicio para aplicar
SELECT pg_reload_conf();
```
