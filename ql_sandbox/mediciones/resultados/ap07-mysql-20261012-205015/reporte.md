# AP-07 vertido a disco — mysql

Corrida `ap07-mysql-20261012-205015` · db_id `medicion-ap07-mysql-20261012-205015` · 5 snapshots cada 60 s · inicio 2026-10-13 01:50:16.858855+00

## Resultado

| Métrica | Valor |
|---|---|
| Positivos detectados | **33/100 (33.0 %)** · IC 95 % 24.6–42.7 % |
| ¿Cumple ≥ 90 %? | **no** |
| Positivos donde la patología se verificó en los contadores | 33/100 |
| Detección entre los verificados | 33/33 (100.0 %) |
| Falsos positivos (controles marcados sin la patología) | 0/40 (0.0 %) |
| Controles marcados en total | 0/40 |
| Casos fuera de la regla detectados | 0/20 |
| Detección por ventana con spill | 132/132 (100.0 %) |
| Controles que sí derramaron (no son FP) | 0 |

## Por categoría

| Categoría | Casos | Patología verificada | Detectados |
|---|---|---|---|
| COUNT(DISTINCT) (solo ordenamiento: fuera de AP-07 en MySQL) | 4 | 4 | 0 (0.0 %) |
| CTE / derivada | 11 | 5 | 5 (45.5 %) |
| DISTINCT | 12 | 0 | 0 (0.0 %) |
| GROUP BY | 36 | 13 | 13 (36.1 %) |
| GROUP BY + ORDER BY | 6 | 3 | 3 (50.0 %) |
| ORDER BY grande (solo ordenamiento: fuera de AP-07 en MySQL) | 10 | 10 | 0 (0.0 %) |
| ROLLUP (solo ordenamiento: fuera de AP-07 en MySQL) | 3 | 3 | 0 (0.0 %) |
| UNION / EXCEPT | 12 | 6 | 6 (50.0 %) |
| agregado ordenado (solo ordenamiento: fuera de AP-07 en MySQL) | 3 | 3 | 0 (0.0 %) |
| subconsulta IN | 5 | 0 | 0 (0.0 %) |
| ventana | 18 | 6 | 6 (33.3 %) |

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

- `G1-t0` (GROUP BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t0 GROUP BY g_hi HAVING COUNT(*) > 4` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G3-t0` (GROUP BY, patología NO verificada): `SELECT s, COUNT(*) FROM ql_m07_t0 GROUP BY s HAVING COUNT(*) > 6` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G4-t0` (GROUP BY, patología NO verificada): `SELECT g_hi, g_lo, MAX(amt) FROM ql_m07_t0 GROUP BY g_hi, g_lo HAVING MAX(amt) > 999` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G6-t0` (GROUP BY, patología NO verificada): `SELECT fk % 40000, AVG(amt) FROM ql_m07_t0 GROUP BY fk % 40000 HAVING AVG(amt) > 995` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t0` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t0) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t0` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t0) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W1-t0` (ventana, patología NO verificada): `SELECT COUNT(*) FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY g_mid ORDER BY amt) AS rn FROM ql_m07_t0) x W` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W3-t0` (ventana, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi, SUM(amt) OVER (PARTITION BY g_hi) AS t FROM ql_m07_t0) x WHERE t > 1500` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U1-t0` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t0 UNION SELECT fk FROM ql_m07_t0) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U2-t0` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t0 EXCEPT SELECT fk FROM ql_m07_t1) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C1-t0` (CTE / derivada, patología NO verificada): `WITH c AS (SELECT g_hi, COUNT(*) AS n FROM ql_m07_t0 GROUP BY g_hi) SELECT COUNT(*) FROM c WHERE n > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `Q1-t0` (subconsulta IN, patología NO verificada): `SELECT COUNT(*) FROM ql_m07_t0 WHERE g_hi IN (SELECT fk FROM ql_m07_t1 WHERE g_lo < 50)` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G1-t1` (GROUP BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t1 GROUP BY g_hi HAVING COUNT(*) > 4` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G4-t1` (GROUP BY, patología NO verificada): `SELECT g_hi, g_lo, MAX(amt) FROM ql_m07_t1 GROUP BY g_hi, g_lo HAVING MAX(amt) > 999` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G5-t1` (GROUP BY, patología NO verificada): `SELECT d, g_mid, COUNT(*) FROM ql_m07_t1 GROUP BY d, g_mid HAVING COUNT(*) > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G6-t1` (GROUP BY, patología NO verificada): `SELECT fk % 40000, AVG(amt) FROM ql_m07_t1 GROUP BY fk % 40000 HAVING AVG(amt) > 995` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G7-t1` (GROUP BY + ORDER BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t1 GROUP BY g_hi ORDER BY COUNT(*) DESC, g_hi LIMIT 5` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t1` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t1) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t1` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t1) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W1-t1` (ventana, patología NO verificada): `SELECT COUNT(*) FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY g_mid ORDER BY amt) AS rn FROM ql_m07_t1) x W` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W3-t1` (ventana, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi, SUM(amt) OVER (PARTITION BY g_hi) AS t FROM ql_m07_t1) x WHERE t > 1500` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U1-t1` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t1 UNION SELECT fk FROM ql_m07_t1) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U2-t1` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t1 EXCEPT SELECT fk FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C1-t1` (CTE / derivada, patología NO verificada): `WITH c AS (SELECT g_hi, COUNT(*) AS n FROM ql_m07_t1 GROUP BY g_hi) SELECT COUNT(*) FROM c WHERE n > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C2-t1` (CTE / derivada, patología NO verificada): `SELECT COUNT(*) FROM (SELECT fk, MIN(d) AS m FROM ql_m07_t1 GROUP BY fk) x WHERE m > '2027-01-01'` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `Q1-t1` (subconsulta IN, patología NO verificada): `SELECT COUNT(*) FROM ql_m07_t1 WHERE g_hi IN (SELECT fk FROM ql_m07_t2 WHERE g_lo < 50)` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G2-t2` (GROUP BY, patología NO verificada): `SELECT g_mid, SUM(amt) FROM ql_m07_t2 GROUP BY g_mid HAVING SUM(amt) > 9000` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t2` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t2` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W2-t2` (ventana, patología NO verificada): `SELECT MAX(r) FROM (SELECT RANK() OVER (ORDER BY amt, id) AS r FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W3-t2` (ventana, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi, SUM(amt) OVER (PARTITION BY g_hi) AS t FROM ql_m07_t2) x WHERE t > 1500` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U1-t2` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t2 UNION SELECT fk FROM ql_m07_t2) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C2-t2` (CTE / derivada, patología NO verificada): `SELECT COUNT(*) FROM (SELECT fk, MIN(d) AS m FROM ql_m07_t2 GROUP BY fk) x WHERE m > '2027-01-01'` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `Q1-t2` (subconsulta IN, patología NO verificada): `SELECT COUNT(*) FROM ql_m07_t2 WHERE g_hi IN (SELECT fk FROM ql_m07_t3 WHERE g_lo < 50)` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G1-t3` (GROUP BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t3 GROUP BY g_hi HAVING COUNT(*) > 4` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G3-t3` (GROUP BY, patología NO verificada): `SELECT s, COUNT(*) FROM ql_m07_t3 GROUP BY s HAVING COUNT(*) > 6` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G4-t3` (GROUP BY, patología NO verificada): `SELECT g_hi, g_lo, MAX(amt) FROM ql_m07_t3 GROUP BY g_hi, g_lo HAVING MAX(amt) > 999` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G6-t3` (GROUP BY, patología NO verificada): `SELECT fk % 40000, AVG(amt) FROM ql_m07_t3 GROUP BY fk % 40000 HAVING AVG(amt) > 995` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G7-t3` (GROUP BY + ORDER BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t3 GROUP BY g_hi ORDER BY COUNT(*) DESC, g_hi LIMIT 5` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t3` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t3) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t3` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t3) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W1-t3` (ventana, patología NO verificada): `SELECT COUNT(*) FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY g_mid ORDER BY amt) AS rn FROM ql_m07_t3) x W` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W2-t3` (ventana, patología NO verificada): `SELECT MAX(r) FROM (SELECT RANK() OVER (ORDER BY amt, id) AS r FROM ql_m07_t3) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W3-t3` (ventana, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi, SUM(amt) OVER (PARTITION BY g_hi) AS t FROM ql_m07_t3) x WHERE t > 1500` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C1-t3` (CTE / derivada, patología NO verificada): `WITH c AS (SELECT g_hi, COUNT(*) AS n FROM ql_m07_t3 GROUP BY g_hi) SELECT COUNT(*) FROM c WHERE n > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `Q1-t3` (subconsulta IN, patología NO verificada): `SELECT COUNT(*) FROM ql_m07_t3 WHERE g_hi IN (SELECT fk FROM ql_m07_t4 WHERE g_lo < 50)` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G1-t4` (GROUP BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t4 GROUP BY g_hi HAVING COUNT(*) > 4` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G2-t4` (GROUP BY, patología NO verificada): `SELECT g_mid, SUM(amt) FROM ql_m07_t4 GROUP BY g_mid HAVING SUM(amt) > 9000` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G3-t4` (GROUP BY, patología NO verificada): `SELECT s, COUNT(*) FROM ql_m07_t4 GROUP BY s HAVING COUNT(*) > 6` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G6-t4` (GROUP BY, patología NO verificada): `SELECT fk % 40000, AVG(amt) FROM ql_m07_t4 GROUP BY fk % 40000 HAVING AVG(amt) > 995` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G7-t4` (GROUP BY + ORDER BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t4 GROUP BY g_hi ORDER BY COUNT(*) DESC, g_hi LIMIT 5` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t4` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t4) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t4` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t4) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W2-t4` (ventana, patología NO verificada): `SELECT MAX(r) FROM (SELECT RANK() OVER (ORDER BY amt, id) AS r FROM ql_m07_t4) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `Q1-t4` (subconsulta IN, patología NO verificada): `SELECT COUNT(*) FROM ql_m07_t4 WHERE g_hi IN (SELECT fk FROM ql_m07_t5 WHERE g_lo < 50)` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G1-t5` (GROUP BY, patología NO verificada): `SELECT g_hi, COUNT(*) FROM ql_m07_t5 GROUP BY g_hi HAVING COUNT(*) > 4` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G2-t5` (GROUP BY, patología NO verificada): `SELECT g_mid, SUM(amt) FROM ql_m07_t5 GROUP BY g_mid HAVING SUM(amt) > 9000` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G3-t5` (GROUP BY, patología NO verificada): `SELECT s, COUNT(*) FROM ql_m07_t5 GROUP BY s HAVING COUNT(*) > 6` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G4-t5` (GROUP BY, patología NO verificada): `SELECT g_hi, g_lo, MAX(amt) FROM ql_m07_t5 GROUP BY g_hi, g_lo HAVING MAX(amt) > 999` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G5-t5` (GROUP BY, patología NO verificada): `SELECT d, g_mid, COUNT(*) FROM ql_m07_t5 GROUP BY d, g_mid HAVING COUNT(*) > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `G6-t5` (GROUP BY, patología NO verificada): `SELECT fk % 40000, AVG(amt) FROM ql_m07_t5 GROUP BY fk % 40000 HAVING AVG(amt) > 995` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D1-t5` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM ql_m07_t5) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `D2-t5` (DISTINCT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM ql_m07_t5) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W2-t5` (ventana, patología NO verificada): `SELECT MAX(r) FROM (SELECT RANK() OVER (ORDER BY amt, id) AS r FROM ql_m07_t5) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `W3-t5` (ventana, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi, SUM(amt) OVER (PARTITION BY g_hi) AS t FROM ql_m07_t5) x WHERE t > 1500` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `U1-t5` (UNION / EXCEPT, patología NO verificada): `SELECT COUNT(*) FROM (SELECT g_hi FROM ql_m07_t5 UNION SELECT fk FROM ql_m07_t5) x` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
- `C1-t5` (CTE / derivada, patología NO verificada): `WITH c AS (SELECT g_hi, COUNT(*) AS n FROM ql_m07_t5 GROUP BY g_hi) SELECT COUNT(*) FROM c WHERE n > 2` — ventanas ejecutada [2, 3, 4, 5], con spill [], en top_impact [1, 2, 3, 4, 5]
