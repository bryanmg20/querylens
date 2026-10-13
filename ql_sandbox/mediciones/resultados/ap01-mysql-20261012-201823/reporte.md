# AP-01 degradacion respecto de la linea base — mysql

Corrida `ap01-mysql-20261012-201823` · db_id `medicion-ap01-mysql-20261012-201823` · 14 snapshots cada 60 s · inicio 2026-10-13 01:18:24.230425+00

## Resultado

| Métrica | Valor |
|---|---|
| Positivos detectados | **94/100 (94.0 %)** · IC 95 % 87.5–97.2 % |
| ¿Cumple ≥ 90 %? | sí |
| Positivos donde la patología se verificó en los contadores | 94/100 |
| Detección entre los verificados | 94/94 (100.0 %) |
| Falsos positivos (controles marcados sin la patología) | 0/40 (0.0 %) |
| Controles marcados en total | 36/40 |
| Marcados antes de degradar | 0 |
| Detectados después de 2 ventanas | 0 |
| Controles marcados que sí se degradaron (contención de la inyección) | 36 |
| Ventana de detección (1 = la primera tras degradar) | 1: 94 |

## Por categoría

| Categoría | Casos | Patología verificada | Detectados |
|---|---|---|---|
| crecimiento de datos | 25 | 25 | 25 (100.0 %) |
| indice eliminado | 35 | 31 | 31 (88.6 %) |
| indice invisible | 15 | 13 | 13 (86.7 %) |
| indice menos selectivo | 25 | 25 | 25 (100.0 %) |

## Controles

| Categoría | Casos | Marcados | Falsos positivos |
|---|---|---|---|
| control en tabla degradada | 12 | 11 | 0 |
| control en tabla sin cambios | 28 | 25 | 0 |

## Positivos no detectados

- `ql_m01_t1.c10` (indice eliminado, patología NO verificada): `SELECT 1 FROM ql_m01_t1 WHERE c10 = 8805 LIMIT 1` — línea base 0 ventanas válidas; después: sin ventanas medibles
- `ql_m01_t2.c2` (indice invisible, patología NO verificada): `SELECT 1 FROM ql_m01_t2 WHERE c2 = 8805 LIMIT 1` — línea base 0 ventanas válidas; después: sin ventanas medibles
- `ql_m01_t3.c3` (indice eliminado, patología NO verificada): `SELECT 1 FROM ql_m01_t3 WHERE c3 = 8805 LIMIT 1` — línea base 0 ventanas válidas; después: sin ventanas medibles
- `ql_m01_t5.c7` (indice eliminado, patología NO verificada): `SELECT 1 FROM ql_m01_t5 WHERE c7 = 8805 LIMIT 1` — línea base 0 ventanas válidas; después: sin ventanas medibles
- `ql_m01_t6.c13` (indice eliminado, patología NO verificada): `SELECT 1 FROM ql_m01_t6 WHERE c13 = 8805 LIMIT 1` — línea base 0 ventanas válidas; después: sin ventanas medibles
- `ql_m01_t7.c2` (indice invisible, patología NO verificada): `SELECT 1 FROM ql_m01_t7 WHERE c2 = 8805 LIMIT 1` — línea base 0 ventanas válidas; después: sin ventanas medibles

## Controles marcados

- `ql_m01_t0.c6` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 8.824 ms vs 0.5 ms (x17.65)): `SELECT id FROM ql_m01_t0 WHERE c6 BETWEEN 8805 AND 8835`
- `ql_m01_t0.c7` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 13.387 ms vs 0.894 ms (x14.98)): `SELECT id, c7 FROM ql_m01_t0 WHERE c7 > 8805 ORDER BY c7 LIMIT 5`
- `ql_m01_t0.c9` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 6.001 ms vs 0.415 ms (x14.45)): `SELECT COUNT(*) FROM ql_m01_t0 WHERE c9 = 8805`
- `ql_m01_t0.c12` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 18.126 ms vs 0.543 ms (x33.39)): `SELECT id FROM ql_m01_t0 WHERE c12 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t1.c5` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 8.113 ms vs 0.575 ms (x14.1)): `SELECT x.id, d.name FROM ql_m01_t1 x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.c5 = 8805`
- `ql_m01_t2.c13` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 5.454 ms vs 0.398 ms (x13.71)): `SELECT MAX(id) FROM ql_m01_t2 WHERE c13 = 8805`
- `ql_m01_t3.c8` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 6.064 ms vs 0.535 ms (x11.33)): `SELECT id, pad FROM ql_m01_t3 WHERE c8 = 8805`
- `ql_m01_t4.c12` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 7.599 ms vs 0.505 ms (x15.04)): `SELECT id FROM ql_m01_t4 WHERE c12 BETWEEN 8805 AND 8835`
- `ql_m01_t5.c4` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 23.782 ms vs 0.857 ms (x27.75)): `SELECT id, c4 FROM ql_m01_t5 WHERE c4 > 8805 ORDER BY c4 LIMIT 5`
- `ql_m01_t5.c13` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 8.55 ms vs 0.495 ms (x17.28)): `SELECT COUNT(*) FROM ql_m01_t5 WHERE c13 = 8805`
- `ql_m01_t7.c7` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 17.286 ms vs 0.436 ms (x39.62)): `SELECT id FROM ql_m01_t7 WHERE c7 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t8.c0` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 7.074 ms vs 0.501 ms (x14.12)): `SELECT id FROM ql_m01_t8 WHERE c0 BETWEEN 8805 AND 8835`
- `ql_m01_t8.c1` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 25.247 ms vs 0.891 ms (x28.32)): `SELECT id, c1 FROM ql_m01_t8 WHERE c1 > 8805 ORDER BY c1 LIMIT 5`
- `ql_m01_t8.c2` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 7.762 ms vs 0.522 ms (x14.86)): `SELECT COUNT(*) FROM ql_m01_t8 WHERE c2 = 8805`
- `ql_m01_t8.c3` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 15.87 ms vs 0.532 ms (x29.82)): `SELECT id FROM ql_m01_t8 WHERE c3 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t8.c4` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 10.417 ms vs 0.488 ms (x21.34)): `SELECT x.id, d.name FROM ql_m01_t8 x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.c4 = 8805`
- `ql_m01_t8.c5` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 8.123 ms vs 0.387 ms (x20.99)): `SELECT MAX(id) FROM ql_m01_t8 WHERE c5 = 8805`
- `ql_m01_t8.c7` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 9.916 ms vs 0.505 ms (x19.65)): `SELECT id, pad FROM ql_m01_t8 WHERE c7 = 8805`
- `ql_m01_t8.c8` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 6.976 ms vs 0.433 ms (x16.1)): `SELECT id FROM ql_m01_t8 WHERE c8 BETWEEN 8805 AND 8835`
- `ql_m01_t8.c9` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 21.616 ms vs 0.787 ms (x27.46)): `SELECT id, c9 FROM ql_m01_t8 WHERE c9 > 8805 ORDER BY c9 LIMIT 5`
- `ql_m01_t8.c10` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 9.898 ms vs 0.502 ms (x19.72)): `SELECT COUNT(*) FROM ql_m01_t8 WHERE c10 = 8805`
- `ql_m01_t8.c11` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 16.61 ms vs 0.519 ms (x32.01)): `SELECT id FROM ql_m01_t8 WHERE c11 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t8.c12` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 12.698 ms vs 0.529 ms (x23.99)): `SELECT x.id, d.name FROM ql_m01_t8 x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.c12 = 8805`
- `ql_m01_t8.c13` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 9.064 ms vs 0.437 ms (x20.76)): `SELECT MAX(id) FROM ql_m01_t8 WHERE c13 = 8805`
- `ql_m01_t9.c1` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 10.3 ms vs 0.578 ms (x17.81)): `SELECT id, pad FROM ql_m01_t9 WHERE c1 = 8805`
- `ql_m01_t9.c2` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 7.716 ms vs 0.5 ms (x15.44)): `SELECT id FROM ql_m01_t9 WHERE c2 BETWEEN 8805 AND 8835`
- `ql_m01_t9.c3` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 24.35 ms vs 0.745 ms (x32.68)): `SELECT id, c3 FROM ql_m01_t9 WHERE c3 > 8805 ORDER BY c3 LIMIT 5`
- `ql_m01_t9.c4` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 8.345 ms vs 0.528 ms (x15.81)): `SELECT COUNT(*) FROM ql_m01_t9 WHERE c4 = 8805`
- `ql_m01_t9.c5` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 21.315 ms vs 0.517 ms (x41.24)): `SELECT id FROM ql_m01_t9 WHERE c5 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t9.c6` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 11.424 ms vs 0.587 ms (x19.44)): `SELECT x.id, d.name FROM ql_m01_t9 x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.c6 = 8805`
- `ql_m01_t9.c7` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 7.48 ms vs 0.414 ms (x18.08)): `SELECT MAX(id) FROM ql_m01_t9 WHERE c7 = 8805`
- `ql_m01_t9.c9` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 10.436 ms vs 0.571 ms (x18.28)): `SELECT id, pad FROM ql_m01_t9 WHERE c9 = 8805`
- `ql_m01_t9.c10` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 7.295 ms vs 0.492 ms (x14.83)): `SELECT id FROM ql_m01_t9 WHERE c10 BETWEEN 8805 AND 8835`
- `ql_m01_t9.c11` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 27.43 ms vs 0.81 ms (x33.85)): `SELECT id, c11 FROM ql_m01_t9 WHERE c11 > 8805 ORDER BY c11 LIMIT 5`
- `ql_m01_t9.c12` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 7.882 ms vs 0.533 ms (x14.79)): `SELECT COUNT(*) FROM ql_m01_t9 WHERE c12 = 8805`
- `ql_m01_t9.c13` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 19.058 ms vs 0.482 ms (x39.52)): `SELECT id FROM ql_m01_t9 WHERE c13 IN (37303, 55537, 52577, 50054, 4135)`
