# A vs B — confiabilidad de los EXPLAIN

Compara si el EXPLAIN que obtiene cada versión del `telemetry_pipeline` alcanza
para que un detector acierte (VP / FP / FN / VN). Se evalúa un antipatrón por
vez y solo los que usan el EXPLAIN (AP-02, AP-03, AP-04, AP-05, AP-08).

| Versión | Rama | De dónde sale el texto del EXPLAIN |
|---|---|---|
| v1 | `feature/detection_engine` | `active_queries`: la query tiene que estar ejecutándose al tomar el snapshot |
| v3 | `feature/detector-engine-api-rest` | PG: `EXPLAIN (GENERIC_PLAN)` sobre `pg_stat_statements.query`; MySQL: `QUERY_SAMPLE_TEXT` |

Las reglas son siempre las del `detection_engine` de esta rama.

## Preparar la versión 3

```bash
git fetch origin feature/detector-engine-api-rest
git worktree add --detach ../querylens-v3 origin/feature/detector-engine-api-rest
```

Usa el venv de `telemetry_pipeline/` de esta rama (mismas dependencias).

## Correr

Con el sandbox levantado (`ql_sandbox`: `docker compose up -d`), desde la raíz:

```bash
python ql_sandbox/explain_ab/run_ab.py list
python ql_sandbox/explain_ab/run_ab.py run ap02 --version v1 --engine both
python ql_sandbox/explain_ab/run_ab.py run ap02 --version v3 --engine both --pipeline-dir ../querylens-v3/telemetry_pipeline
python ql_sandbox/explain_ab/run_ab.py compare ap02
```

`--case <id>` corre un solo caso; `--keep` deja las tablas.

## Qué hace por caso

1. Resetea las estadísticas del motor (el caso no hereda historial ni `QUERY_SAMPLE_TEXT`).
2. Lanza carga continua con la consulta del caso (`--workers` sesiones) durante
   `--warmup` segundos sin snapshots: es el historial que necesita v3 y lo que
   mantiene la query corriendo para que v1 la vea activa.
3. Toma el snapshot con el pipeline indicado; si la query no tiene EXPLAIN,
   reintenta hasta `--attempts` veces.
4. Corta la carga y corre solo la regla del patrón sobre el snapshot.

Resultado por caso: `VP`, `FN`, `FP`, `VN`, o `sin_explain` / `no_candidata` /
`no_encontrada` cuando el detector ni siquiera tuvo un plan que evaluar.
Un caso con `expected: None` (sin esperado definido, ej. mezclas de valores) da
`detecta` / `no_detecta` y no entra en las métricas.

`compare --detail` muestra todos los casos con el plan que vio cada versión
(`explain_source` en v3), filas estimadas de la raíz y filas reales por ejecución.

## Patrones

| Patrón | Qué cubre |
|---|---|
| `ap02_avoidable_full_scan` | AP-02 con SELECT simples de una tabla (9 casos) |
| `ap02b_full_scan_formas` | AP-02 con agregaciones, `LIMIT`, `GROUP BY`, joins y mezclas de valores (15 casos, solo PostgreSQL) |

## Prototipo: variantes de plan en MySQL

`mysql_variants.py` reprocesa los snapshots MySQL de una corrida (hecha con
`--keep`, las tablas y los histogramas tienen que seguir ahí): pide el plan
"genérico" (`PREPARE` con `?`), el del valor más frecuente y el del menos
frecuente del histograma, elige con `select_plan` de la v3 y vuelve a correr la
regla. No es parte de ningún pipeline; ver el docstring.

```bash
python ql_sandbox/explain_ab/run_ab.py run ap02b --version v3_hist --engine mysql --extra histograms --keep --pipeline-dir ../querylens-v3/telemetry_pipeline
telemetry_pipeline/venv/Scripts/python.exe ql_sandbox/explain_ab/mysql_variants.py ap02b --from-version v3_hist --version v3_hist_variantes --pipeline-dir ../querylens-v3/telemetry_pipeline
```

## Prototipo: planes de auto_explain (v4)

`pg_autoexplain.py` lee el log JSON de `auto_explain` con el rol de monitoreo
(`querylens.read_log()`), toma los planes del `query_id` de cada caso dentro de
su ventana (`started_at` / `ended_at` del resultado) y corre la regla con el
último plan registrado; el desglose por forma de plan queda en `autoexplain`.
Requiere el sandbox levantado con el `docker-compose.yml` de
`feature/pg-auto-explain`. Ver el docstring.

## Agregar un patrón

`patterns/<apXX_nombre>/` con `setup_postgres.sql`, `setup_mysql.sql`,
`teardown.sql` y `cases.py` (`RULE` y `CASES`; ver `ap02_avoidable_full_scan/cases.py`).
Las tablas deben empezar con `ql_ab_`. Lo esperado de cada caso se define por el
diseño de los datos, no por el EXPLAIN de ninguna versión.

## Resultados

`results/<patron>/<motor>_<version>.json` (resumen y detalle por caso, con las
filas estimadas del plan que vio el detector) y `results/<patron>/snapshots/`
(snapshots completos, no se versionan).
