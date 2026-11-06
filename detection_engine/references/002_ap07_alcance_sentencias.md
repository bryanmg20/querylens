# AP-07: qué sentencias evalúa (propuesta, sin implementar)

> **Estado:** pendiente de decidir. Por ahora AP-07 queda como está.
> Analizado el 2026-10-12 al revisar los detectores que no usan EXPLAIN.

## 1. Cómo está hoy

`detect_disk_spill` (`anti_patterns/disk_spill.py`) solo recorre `snapshot.top_impact_queries`. Avisa cuando, en la última ventana, aumentó el indicador de escritura a disco de la consulta:

- **Postgres:** `temp_blks_written` (bloques de 8 KB);
- **MySQL:** `SUM_CREATED_TMP_DISK_TABLES` (tablas temporales creadas en disco).

La severidad depende del tiempo que consumió en la ventana: ≥ 60 s `high`, ≥ 5 s `medium`, y si no, `low`.

Recorre solo los candidatos porque, cuando se escribió, únicamente ellos traían `canonic_query`. Desde AP-06 (patrón N+1), **todos los statements traen `canonic_query`**, así que esa razón ya no existe.

## 2. La brecha

El pipeline mete en los candidatos a toda consulta con `disk_spill_indicator > 0` (`stages/selectors.py`, `select_disk_spill_indicator`). Pero las que **no se pueden explicar**, porque no empiezan por `SELECT`/`WITH` o porque escriben o bloquean, van a `non_explainable_candidates`, y AP-07 no mira esa lista. Se pierden aunque AP-07 no use EXPLAIN para nada.

### Evidencia (sandbox Postgres, 2026-10-12)

Prueba con `work_mem = 64kB` y `maintenance_work_mem = 1MB`, leyendo `temp_blks_written` en `pg_stat_statements`:

| Sentencia | Bloques temporales | ¿La ve AP-07 hoy? |
|---|---|---|
| `CREATE TABLE … AS SELECT … GROUP BY … ORDER BY` | 26.942 | No (no explicable) |
| `REINDEX TABLE` | 9.581 | No (no explicable) |
| `UPDATE … WHERE id IN (SELECT … ORDER BY …)` | 5.842 | No (no explicable) |
| `CREATE INDEX` | 3.843 | No (no explicable) |
| `SELECT … GROUP BY … ORDER BY … LIMIT 1` | 0 | No vertió (top-N en memoria) |
| `VACUUM` | 0 | — |

El `UPDATE` y el `CREATE TABLE AS` son vertidos de **trabajo de consulta** que hoy no se reportan.

## 3. Criterio: trabajo de consulta o mantenimiento

El informe define AP-07 como "una operación de ordenamiento o agrupamiento excede la memoria de trabajo". En Postgres hay dos memorias distintas:

| Tipo | Parámetro | Sentencias |
|---|---|---|
| **Trabajo de consulta** | `work_mem` | SELECT, WITH, INSERT, UPDATE, DELETE, MERGE, CREATE TABLE AS, REFRESH MATERIALIZED VIEW |
| **Mantenimiento** | `maintenance_work_mem` | CREATE INDEX, REINDEX, CLUSTER, VACUUM, ANALYZE, ALTER TABLE |

Que un `CREATE INDEX` sobre una tabla grande escriba a disco es esperable, ocurre pocas veces y se ajusta con otra perilla. La recomendación de AP-07 ("revisar filtros, agrupaciones, ordenamientos…") no aplica a esas sentencias.

"Mantenimiento" no es lo mismo que "DDL": `VACUUM` y `ANALYZE` no son DDL, y `CREATE TABLE … AS SELECT` sí es DDL pero su vertido viene del `SELECT` interno, así que cuenta como trabajo de consulta.

**MySQL:** los índices se construyen con archivos de ordenamiento de InnoDB, que no suman a `SUM_CREATED_TMP_DISK_TABLES`, así que ahí el mantenimiento casi no genera ruido. Esto no se probó en el sandbox. El filtro se aplica igual en los dos motores para que la regla sea la misma.

## 4. Opciones

| Opción | Qué hace | A favor | En contra |
|---|---|---|---|
| **1. Todos los statements, solo trabajo de consulta** (recomendada) | Recorre los statements agregados por `query_id` y evalúa solo los comandos de la lista de trabajo de consulta | Recupera DML y `CREATE TABLE AS`; fiel al informe; igual en ambos motores | No avisa del mantenimiento que vierte siempre (caso menor y de otra naturaleza) |
| 2. Todos los statements, sin filtro | Evalúa cualquier sentencia | Lo más simple | Cada `CREATE INDEX`/`REINDEX` genera un hallazgo con una recomendación equivocada |
| 3. Todos, separados por subtipo | `subtipo: "consulta"` / `"mantenimiento"`; el mantenimiento en `low` y con la recomendación de `maintenance_work_mem` | No pierde nada y la recomendación es correcta | Amplía AP-07 más allá del informe; hay que declararlo y validarlo |
| 4. Dejarlo como está | Solo `top_impact_queries` | Sin cambios | Sigue perdiendo DML y `CREATE TABLE AS` |

En la opción 1 conviene una **lista de permitidos** (solo esos comandos) y no de prohibidos: un comando que no esté previsto no genera ruido. El primer comando se lee del `canonic_query`, o de `query_text` si está en "Not available".

## 5. Mínimo de ejecuciones

El informe pide ≥ 5 ejecuciones en la ventana. El código no exige mínimo desde el cambio del 2026-10-07 (una sola regla para ambos motores). El 2026-10-12 se confirmó que **una ejecución basta**: una carga pesada que corre una vez por minuto y escribe a disco es un hallazgo válido. Hay que declararlo en la fila del informe.

## 6. Impacto en los escenarios del sandbox

Con la opción 1, la **preparación** de los escenarios de Postgres empieza a generar hallazgos. En la misma prueba del 2026-10-12:

| Sentencia de preparación | Bloques temporales | Con la opción 1 |
|---|---|---|
| `INSERT INTO ql_scn (k, c) SELECT … FROM generate_series(1, 1000000)` | 1.709 por escenario | **Lo detecta** (INSERT = trabajo de consulta) |
| `CREATE INDEX ql_scn_k ON ql_scn (k)` | 12.237 (en 5 escenarios) | Excluido (mantenimiento) |

`generate_series` materializa el millón de filas antes de insertarlas y no cabe en `work_mem` (4 MB). MySQL no registró tablas temporales en disco durante la preparación.

Consecuencias:

1. **Un hallazgo extra al inicio de cada escenario de Postgres**, que en la validación contaría como falso positivo.
2. **Un PASS falso en el escenario `disk_spill`**: su expectativa es `"ql_scn" in evidencia`, y el INSERT de preparación también menciona `ql_scn`. Pasaría aunque el `GROUP BY` inyectado no se detectara.

Ajustes al banco de pruebas que van junto con la opción 1 (la regla no cambia):

1. `SET work_mem = '256MB';` al inicio de `SETUP_SQL["postgres"]` en `run_scenario.py`, solo para esa sesión. La preparación deja de verter en todos los escenarios.
2. Expectativa del escenario `disk_spill` más precisa: que la consulta detectada sea la del `GROUP BY c`.
3. `cut_snapshot=True` en el escenario `disk_spill`, como en los de AP-06: lo que pase durante la preparación cae en una ventana que no cuenta.

## 7. Otros pendientes de AP-07

- **Fila del informe:** declarar la regla única para ambos motores (aumento > 0), sin mínimo de ejecuciones y, si se adopta, el alcance de la opción 1.
- **`SUM_SORT_MERGE_PASSES`:** el informe lo usa en MySQL pero el pipeline no lo recolecta, así que hoy solo se ven las tablas temporales en disco, no los ordenamientos. Agregarlo toca el colector de MySQL. Ver la sección 8.
- **`temptable_use_mmap` en MySQL 8.0:** hay un rango de tamaños en que la tabla temporal se escribe a disco en archivos mmap sin que `SUM_CREATED_TMP_DISK_TABLES` lo cuente. El escenario lo pone en `OFF` mientras corre. Ver la sección 8.
- **Pipeline:** `select_disk_spill_indicator` usa el acumulado (`> 0`), así que toda consulta que alguna vez vertió queda como candidata a EXPLAIN para siempre. Con la opción 1, AP-07 deja de depender de ese selector.

## 8. Qué ve y qué no ve AP-07 en MySQL (probado el 2026-10-12, MySQL 8.0.46)

MySQL tiene dos mecanismos distintos cuando una operación no cabe en memoria, y cada uno tiene su propio contador.

### 8.1 Ordenamientos (`ORDER BY` sin índice que lo resuelva)

Usan `sort_buffer_size` (256 KB por defecto). Si no alcanza, MySQL ordena por trozos, escribe cada trozo ordenado a un archivo temporal y después los mezcla. Cada mezcla es una "merge pass".

- Contador: `SUM_SORT_MERGE_PASSES`.
- Prueba: `ORDER BY pad` sobre 200.000 filas con `sort_buffer_size = 32 KB` dio **118 merge passes** y **0** en `SUM_CREATED_TMP_DISK_TABLES`.
- **AP-07 no lo ve**, porque el pipeline no recolecta `SUM_SORT_MERGE_PASSES`. Es la brecha más común: con 256 KB de buffer, cualquier ordenamiento mediano va a disco.
- En Postgres no hay esta brecha: `temp_blks_written` incluye ordenamientos, hashes y tablas temporales.

### 8.2 Tablas temporales internas (`GROUP BY`, `DISTINCT`, `UNION`, tablas derivadas)

Se crean con el motor TempTable y pasan por tres niveles según su tamaño:

| Tamaño de la tabla temporal | Dónde queda | ¿Lo cuenta `SUM_CREATED_TMP_DISK_TABLES`? |
|---|---|---|
| Cabe en `temptable_max_ram` (RAM global de TempTable, compartida por todas las sesiones) | Memoria | No (y no hace falta: no hubo vertido) |
| No cabe en RAM pero no supera `tmp_table_size` (16 MB por defecto) | Con `temptable_use_mmap = ON`: **archivos mmap en disco**. Con `OFF`: tabla InnoDB en disco | Con `ON`: **no**. Con `OFF`: sí |
| Supera `tmp_table_size` | Se convierte en tabla InnoDB en disco | Sí |

Prueba con `temptable_use_mmap = ON` (el valor del sandbox) y `temptable_max_ram` en su mínimo (2 MB):

- `GROUP BY` de enteros de 40.000 a 150.000 grupos: **0** en `SUM_CREATED_TMP_DISK_TABLES`, pero `memory/temptable/physical_disk` llegó a **14 MB**. Sí escribió a disco y no se contó.
- `GROUP BY` con `CHAR(100)` en utf8mb4 (unos 400 bytes por fila, más de 16 MB): **1** en `SUM_CREATED_TMP_DISK_TABLES`, con mmap encendido o apagado.

El punto ciego es el **nivel del medio**. `memory/temptable/physical_disk` es un total del servidor, no se puede atribuir a una consulta.

En el sandbox, `temptable_max_ram` está bajado a propósito (`--temptable_max_ram=1048576` en el compose; MySQL lo sube a su mínimo, 2 MB) para que el escenario vierta. En una instalación normal vale 1 GB, así que el nivel del medio aparece menos, pero sigue existiendo con mucha concurrencia, porque esa RAM es compartida.

### 8.3 Qué se podría hacer

| Brecha | Arreglo | Dónde |
|---|---|---|
| Ordenamientos (8.1) | Recolectar `SUM_SORT_MERGE_PASSES` y que AP-07 lo considere: un aumento de las merge passes también es vertido | Colector de MySQL (pipeline) + `disk_spill.py` |
| mmap (8.2) | No se puede ver por consulta. Declararlo como limitación y recomendar al DBA `temptable_use_mmap = OFF` (o `temptable_max_mmap = 0` desde 8.0.23), así el vertido va a InnoDB y se cuenta | Informe + guía de instalación |

Falta comprobar cómo se comportan las versiones más nuevas (8.4): ahí `temptable_use_mmap` ya no existe y el valor por defecto de `temptable_max_mmap` puede ser distinto.
