# Problemas del plan canónico y de la redacción (revisión 2026-10-12)

Revisión hecha desde el lado de los detectores (detection_engine), con una batería de 40 consultas ejecutadas en los dos motores con el `EXPLAIN` y el normalizador reales del pipeline. La batería, su reporte y los planes crudos están en `ql_sandbox/explain_predicados/` (ver su `README.md` para correrla de nuevo).

Cada problema trae qué pasa, un ejemplo de la batería, la solución propuesta en el pipeline y qué detectores afecta. La sección final lista lo que **no** tiene arreglo en el pipeline, para que no se busque ahí.

Detectores mencionados: AP-02 escaneo completo evitable, AP-03 predicado no sargable, AP-04 índice ausente, AP-05 índice no utilizado o redundante, AP-07 vertido a disco, AP-08 error grave de estimación de cardinalidad.

## Resumen y prioridad

| # | Problema | Archivo | Prioridad |
|---|---|---|---|
| 1 | Planes bitmap de Postgres: la tabla queda sin condición y la condición sin tabla | `stages/explain_normalizer.py` | Alta |
| 2 | Tipos de acceso: "recorrer el índice entero" se traduce como búsqueda | `stages/explain_normalizer.py` | Alta |
| 3 | Se pierden partes de la condición (Filter pisado, `index_condition`) | `stages/explain_normalizer.py` | Alta |
| 4 | Reciben plan candidatos que no se ejecutaron en el ciclo | `stages/explain.py` / `runner.py` | Alta |
| 5 | Subconsultas: MySQL copia el texto, Postgres pone una etiqueta | `stages/normalize.py`, `stages/explain_normalizer.py` | Media |
| 6 | Condiciones de join en lugares distintos según el motor | `stages/explain_normalizer.py` | Media |
| 7 | Nombre real de la tabla (Postgres renombra lecturas repetidas) | `stages/explain_normalizer.py` | Media |
| 8 | `estimated_rows` de MySQL es acumulado en joins | `stages/explain_normalizer.py` | Media |
| 9 | MySQL `IN (...)` y `VALUES (...)` del digest no se canonicalizan | `stages/canonicalizers.py` | Media |
| 10 | La redacción pierde si el valor era número o texto | `stages/normalize.py` | Media |
| 11 | `DATE(col)` llega como `CAST(col AS DATE)` en MySQL | `stages/normalize.py` | Baja |
| 12 | Formatos sueltos de MySQL (`-$1`, `INTERVAL '? DAY'`, `<if>(...)`) | `stages/normalize.py` | Baja |
| 13 | Datos que no se recolectan (merge passes, histogramas) | `collectors/*/queries.py` | Según se adopte |

---

## 1. Planes bitmap de Postgres

**Qué pasa.** Cuando Postgres usa un índice en dos pasos (*Bitmap Index Scan* → *Bitmap Heap Scan*), el normalizador crea dos operaciones `scan` y a cada una le falta algo:

- *Bitmap Heap Scan*: trae la tabla, pero `predicate` queda en `None`, porque su condición está en `Recheck Cond` y solo se copian `Filter` e `Index Cond`;
- *Bitmap Index Scan*: trae la condición (`Index Cond`), pero `relation` queda en `None`, porque ese nodo nombra el índice y no la tabla.

Ningún detector puede saber a qué tabla pertenece la condición. Pasó en 10 de los 40 casos: es el plan normal de una igualdad o un `IN` sobre una columna con índice que devuelve cientos o miles de filas.

**Ejemplo** (caso 01, `WHERE status = 'raro'`):

```
scan  relation=ql_pt_orders  access=index_lookup  predicate=None
scan  relation=None          access=index_lookup  predicate=((status)::text = $1)
```

**Solución.** En `PostgresExplainNormalizer._walk_plan`, tratar *Bitmap Heap Scan* + su hijo como **una** lectura de la tabla:

- `predicate` = `Recheck Cond` (más el `Filter` del heap si lo tiene, unidos con `AND`);
- `index_name` = el `Index Name` del *Bitmap Index Scan* hijo (con `BitmapAnd`/`BitmapOr`, los nombres de todos los índices);
- no emitir una operación aparte para el *Bitmap Index Scan*.

**Afecta:** AP-02, AP-03, AP-04, AP-05 (Postgres).

## 2. Tipos de acceso

**Qué pasa.** En MySQL, `access_type = "index"` **recorre el índice entero** (no filtra con él: lo usa para cubrir columnas o para dar un orden), pero se traduce a `index_lookup`, igual que una búsqueda. Postgres muestra esas mismas consultas como `full_table_scan`, así que AP-02 y AP-04 nunca las evalúan en MySQL. Pasó en 8 de 40.

Además quedan en `None` los tipos que no están en el mapa: `index_merge`, `ref_or_null`, `fulltext`, `system`, `unique_subquery`, `index_subquery`.

Comprobado en `sbtest1` (índices `PRIMARY` en `id` y `k_1` en `k`):

| Consulta | `access_type` | `used_key_parts` | `ref` | Filas examinadas | ¿Filtra con el índice? |
|---|---|---|---|---|---|
| `WHERE id = 5` | const | `[id]` | `const` | 1 | sí |
| `WHERE k = 5` | ref | `[k]` | `const` | 1 | sí |
| `WHERE k BETWEEN 5 AND 9` | range | `[k]` | — | 1 | sí |
| `WHERE k = 5 OR id = 7` | index_merge | — | — | 2 | sí |
| join `b.id = a.k` | eq_ref | `[id]` | `a.k` | 1 | sí |
| `WHERE k + 0 = 5` | **index** | `[k]` | — | **9.936** | **no: recorre todo el índice** |
| `ORDER BY k LIMIT 10` | **index** | `[k]` | — | 10 | **no: lo usa para el orden y corta** |
| `WHERE c = 'x'` (sin índice) | ALL | — | — | 9.936 | no |

En Postgres pasa lo mismo con un `Index Scan` / `Index Only Scan` **sin** `Index Cond` (recorre el índice para un `ORDER BY`): hoy también se marca `index_lookup`.

**Solución.** Un valor canónico nuevo, `full_index_scan`, y mapear así en los dos motores:

| Canónico | Postgres | MySQL |
|---|---|---|
| `full_table_scan` | Seq Scan | ALL |
| `full_index_scan` | Index Scan / Index Only Scan **sin** `Index Cond` | index |
| `index_lookup` | Index Scan / Index Only Scan **con** `Index Cond`, Bitmap Heap Scan | const, system, eq_ref, ref, ref_or_null, range, index_merge, unique_subquery, index_subquery, fulltext |

Mandar también `rows_examined_per_scan` de MySQL (sirve para distinguir el `index` que leyó todo del que paró en el `LIMIT`). Cada detector decide después si `full_index_scan` cuenta como escaneo completo.

**Afecta:** AP-02 y AP-04 (sobre todo en MySQL).

## 3. Partes de la condición que se pierden

**Qué pasa.**

- **Postgres:** `Filter` e `Index Cond` se copian al mismo campo `predicate`; si el nodo tiene los dos, el segundo pisa al primero y se pierde el `Filter`.
- **MySQL:** cuando aplica *index condition pushdown*, esa parte de la condición está en `index_condition`, que no se copia. Y la condición con la que busca en el índice nunca aparece como texto (una búsqueda pura llega con `predicate = None`).

**Ejemplo** (caso 18, `WHERE created_at >= '2026-03-01' AND total > 50`):

```
Postgres  predicate=(created_at >= $1)        <- se perdió "total > 50" (era el Filter)
MySQL     predicate=(ql_pt_orders.total > $1) <- se perdió "created_at >= ..." (era index_condition)
```

**Solución.** Guardar las condiciones en campos separados, iguales en los dos motores, y mantener `predicate` como la unión para no romper a los consumidores actuales:

- `index_condition`: lo que se busca en el índice. Postgres: `Index Cond` (o `Recheck Cond` en bitmap). MySQL: `index_condition`, más la condición de la clave reconstruida con `used_key_parts` + `ref` (por ejemplo `used_key_parts=[k]`, `ref=[const]` → `(k = $n)`; en `range` se conoce la columna pero no el operador);
- `filter`: lo que se revisa fila por fila. Postgres: `Filter`. MySQL: `attached_condition`;
- `predicate`: `index_condition AND filter`.

**Afecta:** AP-03, AP-04, AP-05.

## 4. Candidatos que no se ejecutaron

**Qué pasa.** `top_impact_queries` son las 10 consultas con más tiempo **acumulado**. Desde `93beb5e` todas reciben plan (`GENERIC_PLAN` / sample), aunque no se hayan ejecutado desde el ciclo anterior. Una consulta pesada que corrió hace una hora sigue en la lista y sus hallazgos se vuelven a reportar en cada snapshot.

**Evidencia.** En los snapshots 11 y 12 de la base MySQL `db_d3aff52f`, con la carga ya detenida, los 10 candidatos tenían 0 ejecuciones entre un snapshot y el siguiente, y los 10 traían plan.

**Solución.** Dar plan solo a los candidatos con ejecuciones desde el ciclo anterior. `runner.py` corre siempre, así que puede guardar en memoria el `execution_count` (y `counters_epoch`) de cada `query_id` por `db_id` y compararlo en el ciclo siguiente. Además ahorra EXPLAIN.

Los detectores también van a exigir ejecuciones en la ventana (ya lo hace AP-08), pero que el pipeline no los explique evita trabajo y planes viejos.

**Afecta:** AP-02, AP-03, AP-04, AP-05.

## 5. Subconsultas

**Qué pasa.**

- **MySQL** copia la subconsulta **completa** dentro del `attached_condition` de la tabla de afuera.
- **Postgres** pone una etiqueta: `(SubPlan N)` o, en Postgres 17, `(InitPlan N).colM` (forma nueva que el detector no reconoce del todo).

En MySQL, lo que está dentro de la subconsulta parece una condición de la tabla de afuera: los detectores atribuyen columnas y funciones a la tabla equivocada, y el mismo problema sale dos veces (en la tabla de afuera y en la de la subconsulta).

**Ejemplo** (caso 22):

```
Postgres  o: (customer_id = (InitPlan 2).col1)
MySQL     o: (o.customer_id = (SELECT MIN(c.id) FROM ql_pt_customers AS c WHERE (LOWER(c.name) = $1)))
          -> AP-03 reporta LOWER(name) sobre la tabla "o" (es de "c")
```

**Solución.** Que los dos motores manden lo mismo: "aquí va el resultado de una subconsulta".

- En `clean_mysql_explain_predicate_dynamic`, reemplazar cada nodo `exp.Subquery` por una etiqueta con la misma forma que la de Postgres, por ejemplo `(SubPlan N)`;
- en Postgres, unificar `(InitPlan N).colM` a esa misma etiqueta.

**Afecta:** AP-03, AP-04, AP-05.

## 6. Condiciones de join

**Qué pasa.** Postgres pone la condición del join en el nodo del join (`Hash Cond`, `Merge Cond`, `Join Filter`, que ya van al `predicate` de la operación `join`). MySQL la pone en el `attached_condition` de la tabla interna. Los detectores que leen scans la ven solo en MySQL, y la atribuyen a la tabla interna aunque la columna sea de la otra.

**Ejemplo** (caso 39, `JOIN … ON LOWER(c.email) = LOWER(o.note)`):

```
Postgres  join: Merge Cond ((lower(o.note)) = (lower((c.email)::text)))   scans sin esa condición
MySQL     scan c: (LOWER(c.email) = LOWER(o.note))   -> AP-03 reporta "email" y "note" sobre c
```

**Solución.** En MySQL, partir el `attached_condition` por los `AND` del nivel superior: las condiciones que mencionan columnas de **otra** tabla van al `predicate` de la operación `join` correspondiente; en el scan quedan solo las de su tabla.

**Afecta:** AP-03, AP-04.

## 7. Nombre real de la tabla

**Qué pasa.** `relation` es el alias. Cuando Postgres lee la misma tabla dos veces sin alias (por ejemplo en un `UNION`), la segunda se llama `ql_pt_orders_1`, que no coincide con ninguna tabla ni con el texto de la consulta (caso 35).

**Solución.** Agregar `table_name` con el nombre real:

- Postgres: `Relation Name` del nodo;
- MySQL: el JSON solo trae el alias en `table_name`; resolverlo con los alias del texto de la consulta.

`relation` queda como el alias.

**Afecta:** AP-02, AP-04, AP-05.

## 8. `estimated_rows` acumulado en MySQL

**Qué pasa.** En MySQL `estimated_rows` sale de `rows_produced_per_join`, que en un join es el **acumulado** de todas las vueltas: en el caso 39 dio 5.000.000 sobre una tabla de 5.000 filas. En Postgres `Plan Rows` es **por vuelta**. Para la primera tabla del join los dos coinciden; para las internas no.

**Solución.** Para los scans de MySQL, `estimated_rows = rows_examined_per_scan × filtered / 100` (lo que devuelve cada lectura de esa tabla), que es el equivalente al `Plan Rows` de Postgres. `output_rows` de la consulta no cambia.

**Afecta:** AP-02 y AP-04 en joins de MySQL.

## 9. Canonicalización de formas resumidas del digest de MySQL

**Qué pasa.** `DIGEST_TEXT` resume las listas: `WHERE k IN (...)` y `INSERT … VALUES (...) /* , ... */`. sqlglot no las puede leer y `canonic_query` queda en "Not available". Cualquier `SELECT` de MySQL con `IN (lista)` se queda sin texto canónico.

Comprobado: `SELECT c FROM sbtest1 WHERE k IN (...)` → "Not available".

**Solución.** En `clean_mysql_sintax` (o antes de `canonicalize_query`), reemplazar `IN (...)` por `IN ($1)` y `VALUES (...) /* , ... */` por `VALUES ($1)`. Considerar también subir `max_digest_length` (1024 por defecto): los textos más largos llegan cortados.

**Afecta:** todo lo que use `canonic_query` en MySQL (AP-03 sin plan, AP-06, AP-08).

## 10. La redacción pierde el tipo del valor

**Qué pasa.** La redacción cambia todo valor por `$n`. AP-03 detecta en MySQL la conversión implícita "columna de texto o fecha comparada con un número sin comillas" (`telefono = 5551234`), porque MySQL no la muestra en el plan. Con `$n` ya no se sabe si el valor era `5551234` o `'5551234'`, así que ese caso nunca se detecta.

**Solución.** En `clean_mysql_explain_predicate_dynamic` (camino sqlglot y respaldo con regex), redactar los números como `$n` y los textos como `'$n'`. No viaja ningún dato, solo si era número o texto. Actualizar la decisión N-1 de privacidad en `004_decisiones_contrato.md`.

**Afecta:** AP-03 en MySQL.

## 11. `DATE(col)` en MySQL

**Qué pasa.** MySQL muestra `DATE(created_at)` como `CAST(created_at AS DATE)`. AP-03 lo clasifica como conversión en MySQL y como función en Postgres (caso 13).

**Solución.** En `clean_mysql_explain_predicate_dynamic`, reescribir `CAST(col AS DATE)` como `date(col)`, la forma de Postgres.

**Afecta:** AP-03.

## 12. Formatos sueltos de MySQL

**Qué pasa.**

- negativos: `-$1` en vez de `$1` (caso 32);
- intervalos: `INTERVAL '? DAY'` (caso 38);
- marcadores internos sin limpiar: `<if>(found_match(o), (o.id is null), true)` (caso 34); hoy solo se limpia `<cache>`.

**Solución.** En `clean_mysql_explain_predicate_dynamic`: incluir el signo en el valor redactado, redactar el intervalo como `INTERVAL $n` y quitar los marcadores `<...>(...)` (`<if>`, `found_match`, `<is_not_null_test>`, `<in_optimizer>`…).

**Afecta:** AP-03, AP-04 (menor).

## 13. Datos que hoy no se recolectan

Solo si se decide usarlos; ninguno es urgente para el MVP.

| Dato | Para qué | Dónde |
|---|---|---|
| MySQL `SUM_SORT_MERGE_PASSES` | AP-07: los ordenamientos que van a disco (hoy solo se ven las tablas temporales). Probado: `ORDER BY` con buffer chico dio 118 merge passes y 0 en `SUM_CREATED_TMP_DISK_TABLES` | `STATEMENTS_QUERY` de MySQL |
| MySQL columnas con histograma (`information_schema.COLUMN_STATISTICS`: esquema, tabla, columna, tipo y fecha; **sin** el JSON de los buckets, que trae valores reales) | AP-02/AP-04: saber cuándo la estimación es una constante (sin índice ni histograma MySQL usa 10 % para `=`, 11,11 % para `BETWEEN`/`LIKE`, 33 % para `>`, aunque tenga el valor real) | consulta nueva en el colector de MySQL |

---

## Lo que no tiene arreglo en el pipeline

Son límites de lo que el motor sabe o expone. Se declaran como limitaciones de los detectores.

| Límite | Motivo |
|---|---|
| MySQL estima con porcentajes fijos en columnas sin índice ni histograma, aun con el valor real | El motor no tiene información de esa columna; solo lo arregla el DBA creando un histograma o un índice |
| `GENERIC_PLAN` de Postgres no conoce los valores | Es como funciona; las conversiones que agrega Postgres por el tipo del valor (`k = 5.0` → `(k)::numeric`) solo se ven en planes de auto_explain (≥ 50 ms) |
| La estimación depende de la ejecución muestreada | auto_explain y la muestra de MySQL ven una sola ejecución (normalmente la más lenta); con datos desbalanceados el resultado cambia según cuál tocó |
| Filas reales por tabla dentro de un join | Solo con ANALYZE (`auto_explain.log_analyze`), descartado por sobrecosto |
| MySQL con `temptable_use_mmap = ON` no cuenta ciertas tablas temporales en disco | No se anota por consulta (solo `memory/temptable/physical_disk`, total del servidor) |

## Nota sobre el cast `varchar → text`

Postgres escribe `(columna)::text` para las columnas `varchar` en casi cualquier condición (13 de 40 casos), y AP-03 hoy lo reporta como conversión implícita aunque no impide usar el índice. Se va a corregir en el detector (AP-04 ya tiene esa regla en `_bare_column`), así que **no hace falta** tocarlo en el pipeline.
