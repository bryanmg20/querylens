# AP-07 vertido a disco — mysql

Corrida `ap07-mysql-20261012-215447-ram8mib` · db_id `medicion-ap07-mysql-20261012-215447-ram8mib` · 5 snapshots cada 60 s · inicio 2026-10-13 02:54:48.64081+00 · temptable_max_ram 8 MiB durante la corrida

## Resultado

| Métrica | Valor |
|---|---|
| Positivos detectados | **100/100 (100.0 %)** · IC 95 % 96.3–100.0 % |
| ¿Cumple ≥ 90 %? | sí |
| Positivos donde la patología se verificó en los contadores | 100/100 |
| Detección entre los verificados | 100/100 (100.0 %) |
| Falsos positivos (controles marcados sin la patología) | 0/40 (0.0 %) |
| Controles marcados en total | 0/40 |
| Detección por ventana con spill | 400/400 (100.0 %) |
| Controles que sí derramaron (no son FP) | 0 |

## Por categoría

| Categoría | Casos | Patología verificada | Detectados |
|---|---|---|---|
| COUNT(DISTINCT) | 5 | 5 | 5 (100.0 %) |
| CTE / derivada | 8 | 8 | 8 (100.0 %) |
| DISTINCT | 10 | 10 | 10 (100.0 %) |
| GROUP BY | 22 | 22 | 22 (100.0 %) |
| GROUP BY + ORDER BY | 5 | 5 | 5 (100.0 %) |
| ORDER BY grande | 15 | 15 | 15 (100.0 %) |
| ROLLUP | 4 | 4 | 4 (100.0 %) |
| UNION / EXCEPT | 9 | 9 | 9 (100.0 %) |
| agregado ordenado | 4 | 4 | 4 (100.0 %) |
| subconsulta IN | 3 | 3 | 3 (100.0 %) |
| ventana | 15 | 15 | 15 (100.0 %) |

## Controles

| Categoría | Casos | Marcados | Falsos positivos |
|---|---|---|---|
| DISTINCT chico | 6 | 0 | 0 |
| GROUP BY chico | 11 | 0 | 0 |
| conteo | 6 | 0 | 0 |
| filtro + GROUP BY | 6 | 0 | 0 |
| orden por indice | 5 | 0 | 0 |
| top-N | 6 | 0 | 0 |
