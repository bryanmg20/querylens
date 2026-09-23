# QUERYLENS — Sandbox

## Arrancar todo

```bash
docker compose up
```

Esto hace tres cosas solo:
1. Levanta PostgreSQL 17 y MySQL 8.0
2. Espera a que ambos estén listos
3. sysbench crea las tablas, inserta datos e inyecta carga por 2 minutos

Cuando veas `Carga completada` en los logs, las bases están listas.

## Conectarse

**PostgreSQL**
```bash
psql -h localhost -U ql_user -d ql_demo
# password: ql_pass
```

**MySQL**
```bash
mysql -h 127.0.0.1 -u ql_user -pql_pass ql_demo
```

O conéctate con DBeaver / TablePlus usando los mismos datos.

## Volver a inyectar carga cuando quieras

```bash
docker compose start sysbench
```

## Ajustar la carga (opcional)

Edita las primeras líneas de `scripts/run.sh`:

```bash
TABLES=10        # tablas que crea sysbench
TABLE_SIZE=10000 # filas por tabla
THREADS=4        # hilos concurrentes
DURATION=120     # segundos de carga
```

## Apagar y limpiar

```bash
docker compose down -v
```

## Logs de queries lentas (fuente del pipeline)

El sandbox deja los **logs de statements reales** en bind mounts locales que el pipeline lee:

| Motor | Configuración | Archivo local |
|-------|---------------|---------------|
| PostgreSQL | `log_min_duration_statement=100` (ms) + `log_destination=csvlog` | `pg_logs/postgresql.log` |
| MySQL | `slow_query_log=ON`, `long_query_time=1` (s), `log_output=FILE` | `mysql_logs/ql-slow.log` |

Estos umbrales son los del sandbox de desarrollo. Bájalo si las queries simuladas no se capturan
(por ej. PG `50`, MySQL `0.2`), o súbelos en producción. El pipeline lee los dos con
`telemetry_pipeline/config/logs.py`; si el archivo no existe o no matchea, avanza sin él.
Recrea los contenedores (`docker compose up -d --force-recreate`) tras cambiar los umbrales.