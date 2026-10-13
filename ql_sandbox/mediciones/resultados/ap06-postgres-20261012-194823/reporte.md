# AP-06 patron N+1 — postgres

Corrida `ap06-postgres-20261012-194823` · db_id `medicion-ap06-postgres-20261012-194823` · 5 snapshots cada 60 s · inicio 2026-10-13 00:48:24.237079+00

## Resultado

| Métrica | Valor |
|---|---|
| Positivos detectados | **100/100 (100.0 %)** · IC 95 % 96.3–100.0 % |
| ¿Cumple ≥ 90 %? | sí |
| Positivos donde la patología se verificó en los contadores | 100/100 |
| Detección entre los verificados | 100/100 (100.0 %) |
| Falsos positivos (controles marcados sin la patología) | 8/40 (20.0 %) |
| Controles marcados en total | 8/40 |
| Casos fuera de la regla detectados | 7/8 |
| Detección por ventana válida | 400/400 (100.0 %) |
| Padre identificado correctamente | 76/100 |

## Por categoría

| Categoría | Casos | Patología verificada | Detectados |
|---|---|---|---|
| muchos_a_uno | 40 | 40 | 40 (100.0 %) |
| muchos_a_uno (FK compuesta) | 10 | 10 | 10 (100.0 %) |
| muchos_a_uno con cache (fuera de la regla) | 8 | 0 | 7 (87.5 %) |
| uno_a_muchos | 40 | 40 | 40 (100.0 %) |
| uno_a_muchos (FK compuesta) | 10 | 10 | 10 (100.0 %) |

## Controles

| Categoría | Casos | Marcados | Falsos positivos |
|---|---|---|---|
| IN (...) en lote (pares sin N+1) | 5 | 0 | 0 |
| JOIN en una consulta (pares sin N+1) | 5 | 0 | 0 |
| consulta suelta por FK (pares con N+1) | 10 | 3 | 3 |
| consulta suelta por FK (pares sin N+1) | 5 | 2 | 2 |
| detalle 1 + 1 (pares con N+1) | 10 | 0 | 0 |
| detalle 1 + 1 (pares sin N+1) | 5 | 3 | 3 |

## Controles marcados

- `ctl-suelta-p1-1` (consulta suelta por FK (pares con N+1), falso positivo): `SELECT note, id FROM ql_m06_c1 WHERE p_id = 1`
- `ctl-suelta-p5-5` (consulta suelta por FK (pares con N+1), falso positivo): `SELECT amount, id FROM ql_m06_c5 WHERE p_id = 1 AND status >= 1`
- `ctl-suelta-p6-6` (consulta suelta por FK (pares con N+1), falso positivo): `SELECT note, id FROM ql_m06_c6 WHERE p_id = 1 AND status >= 1`
- `ctl-suelta-p10-2` (consulta suelta por FK (pares sin N+1), falso positivo): `SELECT status, amount FROM ql_m06_c10 WHERE p_id = 1`
- `ctl-suelta-p10-4` (consulta suelta por FK (pares sin N+1), falso positivo): `SELECT amount, status, id FROM ql_m06_c10 WHERE p_id = 1`
- `ctl-detalle-p11-0` (detalle 1 + 1 (pares sin N+1), falso positivo): `SELECT status, id FROM ql_m06_c11 WHERE p_id = 1`
- `ctl-detalle-p11-2` (detalle 1 + 1 (pares sin N+1), falso positivo): `SELECT note, amount FROM ql_m06_c11 WHERE p_id = 1`
- `ctl-detalle-p11-4` (detalle 1 + 1 (pares sin N+1), falso positivo): `SELECT status, note FROM ql_m06_c11 WHERE p_id = 1`

## Hallazgos sobre consultas que no son hijas de un caso

- `ctl-suelta-p10-4`: `SELECT amount, status, id FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p10-2`: `SELECT status, amount FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p6-6`: `SELECT note, id FROM ql_m06_c6 WHERE p_id = $1 AND status >= $2`
- `ctl-suelta-p5-5`: `SELECT amount, id FROM ql_m06_c5 WHERE p_id = $1 AND status >= $2`
- `ctl-suelta-p1-1`: `SELECT note, id FROM ql_m06_c1 WHERE p_id = $1`
- `ctl-suelta-p10-4`: `SELECT amount, status, id FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p10-2`: `SELECT status, amount FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p6-6`: `SELECT note, id FROM ql_m06_c6 WHERE p_id = $1 AND status >= $2`
- `ctl-suelta-p1-1`: `SELECT note, id FROM ql_m06_c1 WHERE p_id = $1`
- `ctl-suelta-p5-5`: `SELECT amount, id FROM ql_m06_c5 WHERE p_id = $1 AND status >= $2`
- `ctl-detalle-p11-0`: `SELECT status, id FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-detalle-p11-4`: `SELECT status, note FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-detalle-p11-2`: `SELECT note, amount FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-suelta-p10-4`: `SELECT amount, status, id FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p10-2`: `SELECT status, amount FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p6-6`: `SELECT note, id FROM ql_m06_c6 WHERE p_id = $1 AND status >= $2`
- `ctl-suelta-p1-1`: `SELECT note, id FROM ql_m06_c1 WHERE p_id = $1`
- `ctl-suelta-p5-5`: `SELECT amount, id FROM ql_m06_c5 WHERE p_id = $1 AND status >= $2`
- `ctl-detalle-p11-0`: `SELECT status, id FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-detalle-p11-4`: `SELECT status, note FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-detalle-p11-2`: `SELECT note, amount FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-suelta-p10-4`: `SELECT amount, status, id FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p10-2`: `SELECT status, amount FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p6-6`: `SELECT note, id FROM ql_m06_c6 WHERE p_id = $1 AND status >= $2`
- `ctl-suelta-p1-1`: `SELECT note, id FROM ql_m06_c1 WHERE p_id = $1`
- `ctl-suelta-p5-5`: `SELECT amount, id FROM ql_m06_c5 WHERE p_id = $1 AND status >= $2`
- `ctl-detalle-p11-4`: `SELECT status, note FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-detalle-p11-0`: `SELECT status, id FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-detalle-p11-2`: `SELECT note, amount FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-suelta-p10-4`: `SELECT amount, status, id FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p10-2`: `SELECT status, amount FROM ql_m06_c10 WHERE p_id = $1`
- `ctl-suelta-p6-6`: `SELECT note, id FROM ql_m06_c6 WHERE p_id = $1 AND status >= $2`
- `ctl-suelta-p1-1`: `SELECT note, id FROM ql_m06_c1 WHERE p_id = $1`
- `ctl-suelta-p5-5`: `SELECT amount, id FROM ql_m06_c5 WHERE p_id = $1 AND status >= $2`
- `ctl-detalle-p11-4`: `SELECT status, note FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-detalle-p11-0`: `SELECT status, id FROM ql_m06_c11 WHERE p_id = $1`
- `ctl-detalle-p11-2`: `SELECT note, amount FROM ql_m06_c11 WHERE p_id = $1`
