# QUERYLENS — Sandbox

## Arrancar todo

```bash
docker compose up -d
```

Esto hace tres cosas:
1. Levanta PostgreSQL 17 y MySQL 8.0 (espera a que ambos estén *healthy*)
2. Levanta el contenedor `sysbench`, que solo instala los clientes
   (`sysbench`, `mysql`, `psql`) y se queda esperando
3. Deja los logs de queries en los bind mounts locales (ver abajo)

**Los datos y la carga no se crean solos:** `scripts/run.sh` se ejecuta a mano
(ver siguiente sección).

## Cargar datos e inyectar carga

```bash
docker exec ql_sysbench bash /scripts/run.sh
```

Crea `sbtest1` en ambos motores, inserta las filas e inyecta carga OLTP.
Cuando veas `Load complete. Both databases ready` en la salida, las bases están listas.

## Conectarse

**PostgreSQL**
```bash
psql -h localhost -p 5432 -U ql_user -d ql_demo
# password: ql_pass
```

**MySQL**
```bash
mysql -h 127.0.0.1 -P 3307 -u app_user -papp_pass ql_demo
```

O conéctate con DBeaver / TablePlus usando los mismos datos. El usuario del
pipeline (`querylens_monitor` / `monitor_pass`, puerto `3307`) solo tiene permisos
de lectura.

## Tráfico para los tests de integración

Los tests de MySQL (`test_mysql_statements_*`) leen
`performance_schema.events_statements_summary_by_digest`, y esa tabla **no
persiste**: es memoria del servidor y se descarta en cada apagado
(*"in-memory tables that use no persistent on-disk storage. The contents are
repopulated beginning at server startup and discarded at server shutdown"* —
MySQL 8.0 Ref. Manual, §29). No existe variable que lo cambie y el volumen
`ql_sandbox_mysql_data` guarda los datos de `ql_demo`, no el `performance_schema`.

O sea: **cada `docker compose down` + `up` (o `restart`) deja los digests de MySQL
vacíos** y esos tests fallan con `performance_schema deberia tener digests tras la
batería`. En CI no pasa porque `ci/load.py` genera tráfico antes del pytest.

Para volver a dejarlos con tráfico visible:

```bash
docker exec ql_sysbench bash /scripts/battery.sh 30 0 mysql
```

PostgreSQL no lo necesita: `pg_stat_statements` guarda sus estadísticas en
`postgres_data` (`pg_stat_statements.save=on`), así que sobrevive al reinicio.

## Volver a inyectar carga cuando quieras

```bash
docker exec ql_sysbench bash /scripts/run.sh      # carga OLTP completa
docker exec ql_sysbench bash /scripts/battery.sh  # queries variadas (batería)
```

Ojo: `battery.sh` **trunca** `pg_stat_statements` y la tabla de digests de MySQL
al empezar, para que log, stats y batería compartan ventana de medición.

## Ajustar la carga (opcional)

Edita las primeras líneas de `scripts/run.sh`:

```bash
TABLES=1         # tablas que crea sysbench
TABLE_SIZE=10000 # filas por tabla
THREADS=4        # hilos concurrentes
DURATION=120     # segundos de carga
```

## Apagar y limpiar

```bash
docker compose down      # apaga: MySQL pierde los digests (ver arriba)
docker compose down -v   # apaga y borra los volúmenes: pierde ql_demo y las stats de PG
```

## Logs de queries lentas (fuente del pipeline)

El sandbox deja los **logs de statements reales** en bind mounts locales que el pipeline lee:

| Motor | Configuración (`docker-compose.yml`) | Archivo local |
|-------|--------------------------------------|---------------|
| PostgreSQL | `log_min_duration_statement=0` + `log_destination=csvlog` | `pg_logs/postgresql.csv` |
| MySQL | `slow_query_log=ON`, `long-query-time=0`, `log_output=FILE` | `mysql_logs/ql-slow.log` |

Umbrales de desarrollo: `0` captura todo (el sandbox sirve para ver volumen, no
para simular un umbral de producción; súbelo si el archivo crece demasiado). El
pipeline lee los dos con `telemetry_pipeline/config/logs.py`; si el archivo no
existe o no matchea, avanza sin él. Recrea los contenedores
(`docker compose up -d --force-recreate`) tras cambiar los umbrales.
