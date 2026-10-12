# Correcciones para el detection_engine (respuesta al 007)

Respuesta a `007_problemas_plan_canonico.md`. Cada punto del 007 se revisó contra el pipeline actual. Lo que se podía corregir en el pipeline ya está hecho en la rama `feature/overhead-measure-pipeline` (sección 1). Este documento lista lo que **le toca al detector** (sección 2), con el motivo de por qué no va en el pipeline.

## 1. Qué cambió en el snapshot (ya hecho en el pipeline)

Conviene leer esto antes de tocar los detectores: algunos cambios abren casos nuevos.

| Punto 007 | Cambio | Qué ve el detector |
|---|---|---|
| 1 | Planes bitmap de Postgres | El *Bitmap Heap Scan* trae `predicate = Recheck Cond AND Filter` e `index_name` (si usa un solo índice). Cada *Bitmap Index Scan* hereda `relation`/`table_name` del heap, conserva su `index_name` y trae `predicate = None`. Siguen siendo dos operaciones `scan`. |
| 2 | `access_method` nuevo: `full_index_scan` | Index Scan / Index Only Scan **sin** `Index Cond` y MySQL `index`. `index_merge`, `ref_or_null`, `fulltext`, `system`, `unique_subquery`, `index_subquery` pasan a `index_lookup` (antes `None`). |
| 3 | `predicate` completo | Postgres: `Index Cond AND Filter`. MySQL: `index_condition AND attached_condition`. Antes una pisaba a la otra. **Ahora AP-03 también ve el `Index Cond`** (ver D1). |
| 5 | Subconsultas de MySQL | La subconsulta copiada en el predicado se reemplaza por `(SubPlan N)`, igual que Postgres: `(o.customer_id = (SubPlan 1))`. La subconsulta sigue teniendo su propio scan. |
| 7 | Campo nuevo `table_name` | Postgres: tabla real (`Relation Name`); `relation` sigue siendo el alias (`ql_pt_orders_1` → `table_name = ql_pt_orders`). MySQL: siempre `None`. |
| 8 | `estimated_rows` de MySQL por lectura | `rows_examined_per_scan × filtered / 100`, como `Plan Rows` de Postgres. Antes era el acumulado del join. |
| 9 | `IN (...)` / `VALUES (...)` del digest | `canonic_query` ya no queda en `"Not available"`: `WHERE k IN (...)` → `WHERE k IN ($1)`. `resolve_table_aliases` vuelve a funcionar en esas consultas. |
| 10 | Redacción con tipo (solo MySQL) | Texto/hex/bit → `'$n'`, número → `$n`: `(o.tel = $1)` vs `(o.tel = '$1')`. Ningún valor viaja. |
| 13a | Campo nuevo `sort_merge_passes` en statements | MySQL: `SUM_SORT_MERGE_PASSES`. Postgres: `NULL` (`temp_blks_written` ya incluye los sorts). El pipeline ya selecciona como candidato a quien tenga `sort_merge_passes > 0`. |

Decisiones y semántica completas en `telemetry_pipeline/references/004_decisiones_contrato.md` (secciones "Plan canónico: operaciones de scan", "disk_spill_indicator" y "query_sample_text").

## 2. Lo que le toca al detector

Orden sugerido: D1 primero, después D2–D5, el resto cuando se pueda.

### D1. Ignorar el cast `varchar → text` de Postgres en AP-03 — **prioridad alta**

- **Dónde:** `detection_engine/anti_patterns/non_sargable_predicate.py`, `_is_cast_on_column` (línea ~109).
- **Qué:** no contar como `implicit_cast` un `(col)::text` sobre una columna `varchar`/`character varying` (misma regla que `_bare_column` en AP-04). Es la "Nota sobre el cast" del 007.
- **Por qué primero:** es el falso positivo más frecuente (13 de 40 casos del 007) y ya existía: el `Index Cond` ya llegaba en `predicate` cuando pisaba al `Filter`. Con el punto 3 además llega en el heap de los planes bitmap, ahora con tabla, así que el falso `implicit_cast` se reporta sobre una tabla concreta y con un índice que **sí** se usa.
- **Por qué no en el pipeline:** el cast lo escribe Postgres y es información real del plan; decidir que no impide usar el índice es una regla del detector, que tiene los tipos de columna.

### D2. Conversión implícita de MySQL con `$n` (punto 10)

- **Dónde:** `non_sargable_predicate.py`, `_bare_column_and_literal`, `_bare_column_and_literals_in`, `_bare_column_and_literals_between` (líneas ~114-140).
- **Qué:** hoy exigen `exp.Literal`, y `$1` se parsea como parámetro, así que el camino "caso MySQL" (líneas ~358-366) **nunca se ejecuta**. Tratar el nodo que sqlglot produce para `$n` como literal numérico (`is_string = False`). `'$n'` ya llega como `exp.Literal` con `is_string = True`, así que no reporta, que es lo correcto.
- **Por qué no en el pipeline:** el pipeline ya manda el tipo (sección 1); falta que el detector lo lea.
- **Nota:** `leading_wildcard` sigue sin poder detectarse desde el plan de MySQL (`LIKE '$1'` no dice si empezaba con `%`); era así antes también.

### D3. AP-02 y AP-04 deben exigir ejecuciones en la ventana (punto 4)

- **Dónde:** `avoidable_full_scan.py` (`detect_avoidable_full_scans`) y `missing_index.py` (`detect_missing_indexes`).
- **Qué:** descartar candidatos sin ejecuciones desde el snapshot anterior, como ya hacen AP-03 (`_executed_since_previous_snapshot`) y AP-05 (`_window_activity`). Conviene mover esa función a `statement_history.py` y reutilizarla en los cuatro.
- **Por qué no en el pipeline:** el pipeline no guarda estado entre ciclos (se encola siempre, sin gate) y lo perdería al reiniciar; el detector ya tiene el historial de snapshots. El 007 decía que solo AP-08 lo hacía: AP-03 y AP-05 también, faltan AP-02 y AP-04.

### D4. Condiciones de join en MySQL atribuidas a la tabla equivocada (punto 6)

- **Dónde:** `non_sargable_predicate.py` (`_detect_predicate_subtypes`) y `missing_index.py` (`_filtered_columns`).
- **Qué:** descartar la columna cuando su calificador (`column_node.table`, resuelto con `alias_map`) es una tabla distinta de la del scan. En el caso 39, `LOWER(c.email)` sigue siendo un hallazgo válido sobre `c`; `o.note` no.
- **Por qué no en el pipeline:** la solución del 007 no se puede implementar: el plan canónico de MySQL tiene **una** operación `join` por `nested_loop` (con `predicate` = la bandera `join_condition`), no una por par de tablas, así que no hay "join correspondiente" al que mover la condición. El pipeline conserva el calificador de cada columna (`o.note`), que es lo que el detector necesita.

### D5. `(InitPlan N).colM` de Postgres 17 (punto 5)

- **Dónde:** `_SUBPLAN_RE` en tres archivos: `non_sargable_predicate.py:16`, `missing_index.py:44`, `unused_index.py:22`.
- **Qué:** `r"\((?:hashed\s+)?(?:SubPlan|InitPlan)\s+\d+\)(?:\.col\d+)?"`. Hoy `(InitPlan 2).col1` queda como `NULL.col1`.
- **Por qué no en el pipeline:** el dato llega bien; es un cambio de regex donde ya se limpian las etiquetas. Con esto, la etiqueta `(SubPlan N)` que ahora manda MySQL también queda cubierta.

### D6. Decidir qué hacer con `full_index_scan` (punto 2)

- **Dónde:** AP-02 (`avoidable_full_scan.py:53`), AP-04 (`missing_index.py:242`), severidad en AP-03 (`non_sargable_predicate.py:600`), AP-05 (`unused_index.py:153`).
- **Qué:** decidir si `full_index_scan` cuenta como escaneo completo. Hoy todos comparan con `== "full_table_scan"`, así que lo ignoran (igual que antes, cuando llegaba como `index_lookup`): no hay regresión, pero tampoco ganancia hasta que se adopte. Ojo: un `ORDER BY ... LIMIT` que recorre el índice y corta enseguida también es `full_index_scan` (`estimated_rows` lo distingue).

### D7. AP-07 con `sort_merge_passes` (punto 13a)

- **Dónde:** `disk_spill.py` (la lógica de `delta_spill`, líneas ~76-121).
- **Qué:** calcular el delta de `sort_merge_passes` igual que el de `disk_spill_indicator` y dar una explicación propia ("ordenamiento que fue a disco", recomendación: índice para el `ORDER BY` o `sort_buffer_size`). En Postgres llega `None`.
- **Por qué no en el pipeline:** el pipeline ya recolecta el dato y selecciona el candidato; la ventana y la explicación son del detector.

### D8. Usar `table_name` (punto 7)

- **Dónde:** AP-02, AP-03, AP-04, AP-05 (donde hoy hacen `alias_map.get(relation, relation)`).
- **Qué:** `operation.get("table_name") or alias_map.get(relation, relation)`. Con eso `ql_pt_orders_1` resuelve a `ql_pt_orders`.
- **Por qué no se resuelve solo en el pipeline:** el pipeline ya manda el dato; en MySQL es `None` y el detector sigue usando `alias_map` (MySQL no renombra lecturas repetidas).

### D9. `CAST(col AS DATE)` de MySQL (punto 11) — opcional

- **Dónde:** `non_sargable_predicate.py`, `_is_cast_on_column`.
- **Qué:** en MySQL, clasificar `CAST(col AS DATE)` como `function_on_column` (MySQL muestra así `DATE(col)`). Solo cambia la etiqueta: el hallazgo y la severidad son iguales.
- **Por qué no en el pipeline:** reescribirlo borraría un `CAST` que el usuario sí escribió.

### D10. Tests

`detection_engine` no tiene suite de tests. Cada cambio de D1 a D8 debería entrar con al menos un caso de la batería del 007 (`ql_sandbox/explain_predicados/`, que **no está en el repo**: hay que subirla para poder reproducirla). Los snapshots de entrada se pueden armar con los goldens de `telemetry_pipeline/tests/golden/`.

## 3. Lo que no se hace en ningún lado

| Punto 007 | Motivo |
|---|---|
| 12: `<if>`, `found_match`, `<in_optimizer>` | El detector ya los reemplaza por `NULL` (`_strip_engine_internal_markers`, AP-03 y AP-04). |
| 12: `-($1)`, `INTERVAL '$1 DAY'` | No filtran datos y sqlglot los parsea bien. |
| 13b: histogramas | Ningún detector los usa; queda como limitación declarada. |
| 9: `max_digest_length` | Es configuración del servidor (requiere reinicio) y el rol de QueryLens es de solo lectura: recomendación al DBA. |
| 3: campos separados `index_condition`/`filter`; reconstruir la clave desde `used_key_parts` | Ningún detector necesita saber de qué parte viene la condición; lo que se busca con el índice es sargable por definición. |
| 2: `rows_examined_per_scan` como campo | Ningún detector lo lee; el pipeline lo usa adentro para `estimated_rows` (punto 8). |
