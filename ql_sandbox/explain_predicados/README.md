# Batería de predicados del EXPLAIN

Compara cómo queda el plan canónico (`canonic_explains`) de la **misma consulta** en Postgres y MySQL, y qué leen de sus predicados los detectores AP-03 (predicado no sargable), AP-04 (índice ausente) y AP-05 (índice no utilizado o redundante).

Usa el código real del pipeline (`EXPLAIN` + `stages/explain_normalizer.py` + `stages/normalize.py`) y de los detectores (`_detect_predicate_subtypes`, `_filtered_columns`, `_predicate_columns`). No toca la cola ni `public.hallazgos`.

## Qué hace

**`pred_battery.py`** (corre dentro del servicio `pipeline` del compose):
1. Crea `ql_pt_orders` (100.000 filas) y `ql_pt_customers` (5.000) en el sandbox de los dos motores, con índices en `status`, `created_at`, `customer_id` y `email`.
2. Por cada uno de los 40 casos (`CASES`) saca tres planes:
   - `pg_generic`: `EXPLAIN (GENERIC_PLAN)` sobre el texto con `$n`, como lo deja `pg_stat_statements`;
   - `pg_literal`: `EXPLAIN` con los valores reales, normalizado como un plan de auto_explain (con la redacción de literales);
   - `mysql`: `EXPLAIN FORMAT=JSON` con los valores reales, como la muestra de MySQL.
3. Guarda por cada plan las operaciones `scan` canónicas y los campos crudos del motor (`Filter`, `Index Cond`, `Recheck Cond`, `attached_condition`, `index_condition`…), en `/tmp/pred_battery.json` dentro del contenedor.
4. Borra las tablas al terminar.

**`pred_analyze.py`** (corre con el venv de `detection_engine`): por cada scan muestra el predicado, lo que detecta AP-03, las columnas de igualdad y rango de AP-04 y las columnas de AP-05. Marca con `!` lo que se pierde al normalizar:
- Postgres: `Index Cond` que pisa al `Filter`, `Recheck Cond` sin copiar, `Bitmap Index Scan` sin tabla;
- MySQL: `index_condition` sin copiar, `access_type` sin traducir o `index` (recorre todo el índice) traducido como búsqueda;
- condiciones de join que van al nodo del join y no al scan.

## Requisitos

- Sandbox levantado (`ql_postgres` y `ql_mysql`) y el compose de la raíz (servicio `pipeline`).
- Se conecta como `ql_user` (Postgres) y `root` (MySQL) por `host.docker.internal`, con las contraseñas del sandbox.

## Uso (desde la raíz del repo, Git Bash)

```bash
# 1. Correr la batería dentro del contenedor del pipeline
docker compose cp ql_sandbox/explain_predicados/pred_battery.py pipeline:/tmp/pred_battery.py
MSYS_NO_PATHCONV=1 docker compose exec -w /app pipeline python /tmp/pred_battery.py
docker compose cp pipeline:/tmp/pred_battery.json ql_sandbox/explain_predicados/pred_battery.json

# 2. Analizar con los detectores
cd detection_engine
./venv/Scripts/python.exe ../ql_sandbox/explain_predicados/pred_analyze.py ../ql_sandbox/explain_predicados/pred_battery.json > ../ql_sandbox/explain_predicados/pred_report.txt
```

`pred_analyze.py` solo necesita sqlglot (no psycopg2), así que el venv local de `detection_engine` funciona aunque Windows bloquee la DLL de psycopg2.

## Agregar casos

Cada caso en `CASES` es `(nombre, sql_postgres_con_$n, sql_postgres_con_valores, sql_mysql_con_valores)`:
- poner `None` en una posición para saltar ese motor;
- si el SQL de MySQL es `None` y el de Postgres con valores no, se usa el mismo texto para MySQL.

Los tipos de columna que usa AP-03 para deducir conversiones están en `TYPES`, en `pred_analyze.py`. Si se agregan tablas o columnas, hay que sumarlas ahí.

## Resultados de la primera corrida (2026-10-12)

| # | Problema | Dónde |
|---|---|---|
| 1 | Planes bitmap de Postgres: la tabla queda sin condición y la condición sin tabla (10/40) | pipeline |
| 2 | MySQL `access_type = index` (recorre todo el índice) traducido como `index_lookup` (8/40) | pipeline |
| 3 | Postgres: `Index Cond` pisa al `Filter`; MySQL: `index_condition` no se copia | pipeline |
| 4 | Subconsultas: MySQL copia el texto completo en el predicado de afuera; Postgres pone `(SubPlan N)` / `(InitPlan N).colM` | pipeline o detector |
| 5 | Condiciones de join: Postgres en el nodo del join, MySQL en el scan de la tabla interna | pipeline o detector |
| 6 | AP-03: `(columna)::text` de los `varchar` en Postgres se reporta como `implicit_cast` (13/40) | detector |
| 7 | `DATE(col)` llega como `CAST(col AS DATE)` en MySQL: subtipo distinto en AP-03 | pipeline o detector |
| 8 | `UNION` en Postgres: la segunda lectura de la tabla se llama `ql_pt_orders_1` | pipeline |
| 9 | MySQL: `-$1`, `INTERVAL '? DAY'`, marcadores `<if>(found_match(...))` sin limpiar | pipeline |

También se confirmó que MySQL usa porcentajes fijos de `filtered` aunque tenga el valor real (10 % para `=`, 11,11 % para `BETWEEN`/`LIKE`, 33 % para `>`), y que `rows_produced_per_join` puede superar el tamaño de la tabla en un join (5.000.000 sobre 5.000 filas).
