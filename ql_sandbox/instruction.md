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

### Otras versiones del motor

PostgreSQL 15, 16, 18 y 19 y MySQL 8.4 tienen su propio servicio, cada uno con
su profile; `docker compose up -d` sin profile levanta solo 17 y 8.0, como
siempre:

```bash
docker compose --profile pg15 up -d      # ql_postgres15, puerto 5415
docker compose --profile mysql84 up -d   # ql_mysql84, puerto 3384
```

| Profile | Servicio | Puerto en el host |
|---|---|---|
| `pg15` | `postgres15` | 5415 |
| `pg16` | `postgres16` | 5416 |
| `pg18` | `postgres18` | 5418 |
| `pg19` | `postgres19` (release candidate, `PG19_TAG`) | 5419 |
| `mysql84` | `mysql84` | 3384 |

Los scripts de `scripts/` apuntan a otra versión con `PG_HOST` o `MYSQL_HOST`
(el nombre del servicio), por ejemplo
`docker exec -e PG_HOST=postgres15 ql_sysbench bash /scripts/postgres/simulate_ct01_deadlock_order_postgres.sh`.

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

## Claves foráneas (sección `foreign_keys`)

sysbench no define claves foráneas, así que `foreign_keys` llega vacío. Para que
traiga filas:

```bash
docker exec ql_sysbench bash /scripts/postgres/create_foreign_keys_postgres.sh
docker exec ql_sysbench bash /scripts/mysql/create_foreign_keys_mysql.sh
```

Crean en `ql_demo` (con el usuario de la app) una FK simple
(`ql_fk_orders.customer_id → ql_fk_customers.id`) y una compuesta
(`ql_fk_stock(warehouse_region, warehouse_code) → ql_fk_warehouses(region, code)`):
3 filas por motor en el snapshot. Se pueden volver a ejecutar sin problema; con `drop`
al final borran las tablas `ql_fk_*`.

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

## Logs de statements (retirados)

El sandbox ya **no** escribe el log de todas las sentencias de PostgreSQL
(`log_min_duration_statement=0`, csvlog en `pg_logs/`) ni el slow log de MySQL
(`long-query-time=0` en `mysql_logs/`). Eran la fuente de la "Opción B" (leer el
texto real desde logs del servidor), descartada: el pipeline obtiene el plan con
`EXPLAIN` directo en cada motor. Ver
`telemetry_pipeline/references/005_evaluacion_alternativas_query_real.md`.

## Permisos del rol monitor

`querylens_monitor` es **solo lectura** (`SELECT` + lectura de estadísticas): el
pipeline explica únicamente `SELECT`/`WITH`. Los candidatos `INSERT`/`UPDATE`/
`DELETE` viajan en `non_explainable_candidates`, sin plan.

Los scripts de `init/` solo corren al crear el volumen: un sandbox creado antes
de este cambio conserva los grants DML hasta recrearlo (`docker compose down -v`)
o revocarlos a mano.
