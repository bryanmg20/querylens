# AP-07 vertido a disco — mysql

Corrida `ap07-mysql-20261012-211225` · db_id `medicion-ap07-mysql-20261012-211225` · 5 snapshots cada 60 s · inicio 2026-10-13 02:12:26.685459+00

## Resultado

| Métrica | Valor |
|---|---|
| Positivos detectados | **58/100 (58.0 %)** · IC 95 % 48.2–67.2 % |
| ¿Cumple ≥ 90 %? | **no** |
| Positivos donde la patología se verificó en los contadores | 58/100 |
| Detección entre los verificados | 58/58 (100.0 %) |
| Falsos positivos (controles marcados sin la patología) | 0/40 (0.0 %) |
| Controles marcados en total | 0/40 |
| Detección por ventana con spill | 232/232 (100.0 %) |
| Controles que sí derramaron (no son FP) | 0 |

## Por categoría

| Categoría | Casos | Patología verificada | Detectados |
|---|---|---|---|
| COUNT(DISTINCT) | 4 | 4 | 4 (100.0 %) |
| CTE / derivada | 8 | 4 | 4 (50.0 %) |
| DISTINCT | 9 | 1 | 1 (11.1 %) |
| GROUP BY | 30 | 11 | 11 (36.7 %) |
| GROUP BY + ORDER BY | 5 | 2 | 2 (40.0 %) |
| ORDER BY grande | 12 | 12 | 12 (100.0 %) |
| ROLLUP | 4 | 4 | 4 (100.0 %) |
| UNION / EXCEPT | 8 | 2 | 2 (25.0 %) |
| agregado ordenado | 4 | 4 | 4 (100.0 %) |
| subconsulta IN | 4 | 2 | 2 (50.0 %) |
| ventana | 12 | 12 | 12 (100.0 %) |

## Controles

| Categoría | Casos | Marcados | Falsos positivos |
|---|---|---|---|
| DISTINCT chico | 6 | 0 | 0 |
| GROUP BY chico | 11 | 0 | 0 |
| conteo | 6 | 0 | 0 |
| filtro + GROUP BY | 6 | 0 | 0 |
| orden por indice | 5 | 0 | 0 |
| top-N | 6 | 0 | 0 |

## Positivos no detectados

- `G2-t0` (GROUP BY, patología NO verificada): `SELECT g_mid, SUM(amt) FROM ql_m07_t0 GROUP BY g_mid HAVING SUM(amt) > 9000` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G3-t0` (GROUP BY, patología NO verificada): `SELECT s, COUNT(*) FROM ql_m07_t0 GROUP BY s HAVING COUNT(*) > 6` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G4-t0` (GROUP BY, patología NO verificada): `SELECT g_hi, g_lo, MAX(amt) FROM ql_m07_t0 GROUP BY g_hi, g_lo HAVING MAX(amt) > 999` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G5-t0` (GROUP BY, patología NO verificada): `SELECT d, g_mid, COUNT(*) FROM ql_m07_t0 GROUP BY d, g_mid HAVING COUNT(*) > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G7-t0` (GROUP BY + ORDER BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t0 GROUP BY g_hi ORDER BY COUNT(*) DESC, g_hi LIMIT 5` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t0` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t0) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t0` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t0) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U1-t0` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t0 UNION SELECT fk FROM ql_m07_t0) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C1-t0` (CTE / derivada, patología NO verificada): `WITH c AS (SELECT g_hi, COUNT(*) AS n FROM ql_m07_t0 GROUP BY g_hi) SELECT COUNT(*) FROM c WHERE n > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C2-t0` (CTE / derivada, patología NO verificada): `SELECT COUNT(*) FROM (SELECT fk, MIN(d) AS m FROM ql_m07_t0 GROUP BY fk) x WHERE m > '2027-01-01'` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G1-t1` (GROUP BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t1 GROUP BY g_hi HAVING COUNT(*) > 4` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G3-t1` (GROUP BY, patología NO verificada): `SELECT s, COUNT(*) FROM ql_m07_t1 GROUP BY s HAVING COUNT(*) > 6` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G5-t1` (GROUP BY, patología NO verificada): `SELECT d, g_mid, COUNT(*) FROM ql_m07_t1 GROUP BY d, g_mid HAVING COUNT(*) > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G7-t1` (GROUP BY + ORDER BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t1 GROUP BY g_hi ORDER BY COUNT(*) DESC, g_hi LIMIT 5` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t1` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t1) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U2-t1` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t1 EXCEPT SELECT fk FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G2-t2` (GROUP BY, patología NO verificada): `SELECT g_mid, SUM(amt) FROM ql_m07_t2 GROUP BY g_mid HAVING SUM(amt) > 9000` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G4-t2` (GROUP BY, patología NO verificada): `SELECT g_hi, g_lo, MAX(amt) FROM ql_m07_t2 GROUP BY g_hi, g_lo HAVING MAX(amt) > 999` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G5-t2` (GROUP BY, patología NO verificada): `SELECT d, g_mid, COUNT(*) FROM ql_m07_t2 GROUP BY d, g_mid HAVING COUNT(*) > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G6-t2` (GROUP BY, patología NO verificada): `SELECT fk % 40000, AVG(amt) FROM ql_m07_t2 GROUP BY fk % 40000 HAVING AVG(amt) > 995` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t2` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t2` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U1-t2` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t2 UNION SELECT fk FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U2-t2` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t2 EXCEPT SELECT fk FROM ql_m07_t3) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C1-t2` (CTE / derivada, patología NO verificada): `WITH c AS (SELECT g_hi, COUNT(*) AS n FROM ql_m07_t2 GROUP BY g_hi) SELECT COUNT(*) FROM c WHERE n > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `Q1-t2` (subconsulta IN, patología NO verificada): `SELECT COUNT(*) FROM ql_m07_t2 WHERE g_hi IN (SELECT fk FROM ql_m07_t3 WHERE g_lo < 50)` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G1-t3` (GROUP BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t3 GROUP BY g_hi HAVING COUNT(*) > 4` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G2-t3` (GROUP BY, patología NO verificada): `SELECT g_mid, SUM(amt) FROM ql_m07_t3 GROUP BY g_mid HAVING SUM(amt) > 9000` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G3-t3` (GROUP BY, patología NO verificada): `SELECT s, COUNT(*) FROM ql_m07_t3 GROUP BY s HAVING COUNT(*) > 6` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G4-t3` (GROUP BY, patología NO verificada): `SELECT g_hi, g_lo, MAX(amt) FROM ql_m07_t3 GROUP BY g_hi, g_lo HAVING MAX(amt) > 999` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G5-t3` (GROUP BY, patología NO verificada): `SELECT d, g_mid, COUNT(*) FROM ql_m07_t3 GROUP BY d, g_mid HAVING COUNT(*) > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G7-t3` (GROUP BY + ORDER BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t3 GROUP BY g_hi ORDER BY COUNT(*) DESC, g_hi LIMIT 5` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t3` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t3) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t3` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t3) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U1-t3` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t3 UNION SELECT fk FROM ql_m07_t3) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U2-t3` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t3 EXCEPT SELECT fk FROM ql_m07_t4) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C2-t3` (CTE / derivada, patología NO verificada): `SELECT COUNT(*) FROM (SELECT fk, MIN(d) AS m FROM ql_m07_t3 GROUP BY fk) x WHERE m > '2027-01-01'` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `Q1-t3` (subconsulta IN, patología NO verificada): `SELECT COUNT(*) FROM ql_m07_t3 WHERE g_hi IN (SELECT fk FROM ql_m07_t4 WHERE g_lo < 50)` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G3-t4` (GROUP BY, patología NO verificada): `SELECT s, COUNT(*) FROM ql_m07_t4 GROUP BY s HAVING COUNT(*) > 6` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G4-t4` (GROUP BY, patología NO verificada): `SELECT g_hi, g_lo, MAX(amt) FROM ql_m07_t4 GROUP BY g_hi, g_lo HAVING MAX(amt) > 999` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G5-t4` (GROUP BY, patología NO verificada): `SELECT d, g_mid, COUNT(*) FROM ql_m07_t4 GROUP BY d, g_mid HAVING COUNT(*) > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t4` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t4) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
