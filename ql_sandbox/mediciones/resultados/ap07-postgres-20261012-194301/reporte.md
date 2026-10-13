# AP-07 vertido a disco — postgres

Corrida `ap07-postgres-20261012-194301` · db_id `medicion-ap07-postgres-20261012-194301` · 5 snapshots cada 60 s · inicio 2026-10-13 00:43:02.555226+00

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
| COUNT(DISTINCT) | 8 | 8 | 8 (100.0 %) |
| CTE / derivada | 8 | 8 | 8 (100.0 %) |
| DISTINCT | 8 | 8 | 8 (100.0 %) |
| GROUP BY | 24 | 24 | 24 (100.0 %) |
| GROUP BY + ORDER BY | 4 | 4 | 4 (100.0 %) |
| ORDER BY grande | 12 | 12 | 12 (100.0 %) |
| ROLLUP | 3 | 3 | 3 (100.0 %) |
| UNION / EXCEPT | 8 | 8 | 8 (100.0 %) |
| agregado ordenado | 6 | 6 | 6 (100.0 %) |
| hash join | 4 | 4 | 4 (100.0 %) |
| subconsulta IN | 3 | 3 | 3 (100.0 %) |
| ventana | 12 | 12 | 12 (100.0 %) |

## Controles

| Categoría | Casos | Marcados | Falsos positivos |
|---|---|---|---|
| DISTINCT chico | 6 | 0 | 0 |
| GROUP BY chico | 6 | 0 | 0 |
| GROUP BY grande con memoria | 5 | 0 | 0 |
| conteo | 6 | 0 | 0 |
| filtro + GROUP BY | 6 | 0 | 0 |
| orden por indice | 5 | 0 | 0 |
| top-N | 6 | 0 | 0 |
