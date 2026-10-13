# AP-01 degradacion respecto de la linea base — mysql

Corrida `ap01-mysql-20261012-225934` · db_id `medicion-ap01-mysql-20261012-225934` · 14 snapshots cada 60 s · inicio 2026-10-13 03:59:35.666356+00

## Resultado

| Métrica | Valor |
|---|---|
| Positivos detectados | **100/100 (100.0 %)** · IC 95 % 96.3–100.0 % |
| ¿Cumple ≥ 90 %? | sí |
| Positivos donde la patología se verificó en los contadores | 100/100 |
| Detección entre los verificados | 100/100 (100.0 %) |
| Falsos positivos (controles marcados sin la patología) | 0/40 (0.0 %) |
| Controles marcados en total | 40/40 |
| Marcados antes de degradar | 0 |
| Detectados después de 2 ventanas | 0 |
| Controles marcados que sí se degradaron (contención de la inyección) | 40 |
| Ventana de detección (1 = la primera tras degradar) | 1: 100 |

## Por categoría

| Categoría | Casos | Patología verificada | Detectados |
|---|---|---|---|
| crecimiento de datos | 25 | 25 | 25 (100.0 %) |
| indice eliminado | 35 | 35 | 35 (100.0 %) |
| indice invisible | 15 | 15 | 15 (100.0 %) |
| indice menos selectivo | 25 | 25 | 25 (100.0 %) |

## Controles

| Categoría | Casos | Marcados | Falsos positivos |
|---|---|---|---|
| control en tabla degradada | 12 | 12 | 0 |
| control en tabla sin cambios | 28 | 28 | 0 |

## Controles marcados

- `ql_m01_t0.c6` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 4.868 ms vs 0.694 ms (x7.02)): `SELECT id FROM ql_m01_t0 WHERE c6 BETWEEN 8805 AND 8835`
- `ql_m01_t0.c7` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 12.958 ms vs 1.306 ms (x9.92)): `SELECT id, c7 FROM ql_m01_t0 WHERE c7 > 8805 ORDER BY c7 LIMIT 5`
- `ql_m01_t0.c9` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 5.771 ms vs 0.73 ms (x7.9)): `SELECT COUNT(*) FROM ql_m01_t0 WHERE c9 = 8805`
- `ql_m01_t0.c12` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 8.072 ms vs 0.737 ms (x10.95)): `SELECT id FROM ql_m01_t0 WHERE c12 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t1.c5` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 5.933 ms vs 0.812 ms (x7.31)): `SELECT x.id, d.name FROM ql_m01_t1 x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.c5 = 8805`
- `ql_m01_t2.c13` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 6.272 ms vs 0.598 ms (x10.5)): `SELECT MAX(id) FROM ql_m01_t2 WHERE c13 = 8805`
- `ql_m01_t3.c4` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 5.94 ms vs 0.631 ms (x9.42)): `SELECT 1 FROM ql_m01_t3 WHERE c4 = 8805 LIMIT 1`
- `ql_m01_t3.c8` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 4.38 ms vs 0.708 ms (x6.19)): `SELECT id, pad FROM ql_m01_t3 WHERE c8 = 8805`
- `ql_m01_t4.c12` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 4.6 ms vs 0.682 ms (x6.75)): `SELECT id FROM ql_m01_t4 WHERE c12 BETWEEN 8805 AND 8835`
- `ql_m01_t5.c4` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 12.299 ms vs 1.221 ms (x10.07)): `SELECT id, c4 FROM ql_m01_t5 WHERE c4 > 8805 ORDER BY c4 LIMIT 5`
- `ql_m01_t5.c13` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 4.74 ms vs 0.744 ms (x6.37)): `SELECT COUNT(*) FROM ql_m01_t5 WHERE c13 = 8805`
- `ql_m01_t7.c7` (control en tabla degradada, la patología ocurrió (ver contadores); v12: 8.195 ms vs 0.827 ms (x9.9)): `SELECT id FROM ql_m01_t7 WHERE c7 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t8.c0` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 4.432 ms vs 0.739 ms (x5.99)): `SELECT id FROM ql_m01_t8 WHERE c0 BETWEEN 8805 AND 8835`
- `ql_m01_t8.c1` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 11.19 ms vs 1.359 ms (x8.24)): `SELECT id, c1 FROM ql_m01_t8 WHERE c1 > 8805 ORDER BY c1 LIMIT 5`
- `ql_m01_t8.c2` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 4.139 ms vs 0.674 ms (x6.14)): `SELECT COUNT(*) FROM ql_m01_t8 WHERE c2 = 8805`
- `ql_m01_t8.c3` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 8.03 ms vs 0.719 ms (x11.17)): `SELECT id FROM ql_m01_t8 WHERE c3 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t8.c4` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 6.883 ms vs 0.776 ms (x8.87)): `SELECT x.id, d.name FROM ql_m01_t8 x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.c4 = 8805`
- `ql_m01_t8.c5` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 5.652 ms vs 0.615 ms (x9.19)): `SELECT MAX(id) FROM ql_m01_t8 WHERE c5 = 8805`
- `ql_m01_t8.c6` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 5.081 ms vs 0.726 ms (x7.0)): `SELECT 1 FROM ql_m01_t8 WHERE c6 = 8805 LIMIT 1`
- `ql_m01_t8.c7` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 6.893 ms vs 0.702 ms (x9.81)): `SELECT id, pad FROM ql_m01_t8 WHERE c7 = 8805`
- `ql_m01_t8.c8` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 6.455 ms vs 0.741 ms (x8.72)): `SELECT id FROM ql_m01_t8 WHERE c8 BETWEEN 8805 AND 8835`
- `ql_m01_t8.c9` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 8.633 ms vs 1.369 ms (x6.3)): `SELECT id, c9 FROM ql_m01_t8 WHERE c9 > 8805 ORDER BY c9 LIMIT 5`
- `ql_m01_t8.c10` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 6.019 ms vs 0.767 ms (x7.85)): `SELECT COUNT(*) FROM ql_m01_t8 WHERE c10 = 8805`
- `ql_m01_t8.c11` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 8.454 ms vs 0.745 ms (x11.35)): `SELECT id FROM ql_m01_t8 WHERE c11 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t8.c12` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 8.421 ms vs 0.838 ms (x10.05)): `SELECT x.id, d.name FROM ql_m01_t8 x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.c12 = 8805`
- `ql_m01_t8.c13` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 4.778 ms vs 0.587 ms (x8.14)): `SELECT MAX(id) FROM ql_m01_t8 WHERE c13 = 8805`
- `ql_m01_t9.c0` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 3.823 ms vs 0.673 ms (x5.68)): `SELECT 1 FROM ql_m01_t9 WHERE c0 = 8805 LIMIT 1`
- `ql_m01_t9.c1` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 7.715 ms vs 0.693 ms (x11.14)): `SELECT id, pad FROM ql_m01_t9 WHERE c1 = 8805`
- `ql_m01_t9.c2` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 4.401 ms vs 0.687 ms (x6.41)): `SELECT id FROM ql_m01_t9 WHERE c2 BETWEEN 8805 AND 8835`
- `ql_m01_t9.c3` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 9.763 ms vs 1.375 ms (x7.1)): `SELECT id, c3 FROM ql_m01_t9 WHERE c3 > 8805 ORDER BY c3 LIMIT 5`
- `ql_m01_t9.c4` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 5.281 ms vs 0.691 ms (x7.64)): `SELECT COUNT(*) FROM ql_m01_t9 WHERE c4 = 8805`
- `ql_m01_t9.c5` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 9.491 ms vs 0.725 ms (x13.09)): `SELECT id FROM ql_m01_t9 WHERE c5 IN (37303, 55537, 52577, 50054, 4135)`
- `ql_m01_t9.c6` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 6.891 ms vs 0.772 ms (x8.93)): `SELECT x.id, d.name FROM ql_m01_t9 x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.c6 = 8805`
- `ql_m01_t9.c7` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 5.742 ms vs 0.552 ms (x10.41)): `SELECT MAX(id) FROM ql_m01_t9 WHERE c7 = 8805`
- `ql_m01_t9.c8` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 5.961 ms vs 0.669 ms (x8.91)): `SELECT 1 FROM ql_m01_t9 WHERE c8 = 8805 LIMIT 1`
- `ql_m01_t9.c9` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 6.697 ms vs 0.816 ms (x8.21)): `SELECT id, pad FROM ql_m01_t9 WHERE c9 = 8805`
- `ql_m01_t9.c10` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 4.406 ms vs 0.733 ms (x6.01)): `SELECT id FROM ql_m01_t9 WHERE c10 BETWEEN 8805 AND 8835`
- `ql_m01_t9.c11` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 11.589 ms vs 1.196 ms (x9.69)): `SELECT id, c11 FROM ql_m01_t9 WHERE c11 > 8805 ORDER BY c11 LIMIT 5`
- `ql_m01_t9.c12` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 6.159 ms vs 0.821 ms (x7.5)): `SELECT COUNT(*) FROM ql_m01_t9 WHERE c12 = 8805`
- `ql_m01_t9.c13` (control en tabla sin cambios, la patología ocurrió (ver contadores); v12: 8.172 ms vs 0.786 ms (x10.39)): `SELECT id FROM ql_m01_t9 WHERE c13 IN (37303, 55537, 52577, 50054, 4135)`
