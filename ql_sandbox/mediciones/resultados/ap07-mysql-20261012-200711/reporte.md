# AP-07 vertido a disco — mysql

Corrida `ap07-mysql-20261012-200711` · db_id `medicion-ap07-mysql-20261012-200711` · 5 snapshots cada 60 s · inicio 2026-10-13 01:07:11.769905+00

## Resultado

| Métrica | Valor |
|---|---|
| Positivos detectados | **100/100 (100.0 %)** · IC 95 % 96.3–100.0 % |
| ¿Cumple ≥ 90 %? | sí |
| Positivos donde la patología se verificó en los contadores | 100/100 |
| Detección entre los verificados | 100/100 (100.0 %) |
| Falsos positivos (controles marcados sin la patología) | 0/40 (0.0 %) |
| Controles marcados en total | 0/40 |
| Casos fuera de la regla detectados | 0/20 |
| Detección por ventana con spill | 400/400 (100.0 %) |
| Controles que sí derramaron (no son FP) | 0 |

## Por categoría

| Categoría | Casos | Patología verificada | Detectados |
|---|---|---|---|
| COUNT(DISTINCT) (solo ordenamiento: fuera de AP-07 en MySQL) | 4 | 4 | 0 (0.0 %) |
| CTE / derivada | 11 | 11 | 11 (100.0 %) |
| DISTINCT | 12 | 12 | 12 (100.0 %) |
| GROUP BY | 36 | 36 | 36 (100.0 %) |
| GROUP BY + ORDER BY | 6 | 6 | 6 (100.0 %) |
| ORDER BY grande (solo ordenamiento: fuera de AP-07 en MySQL) | 10 | 10 | 0 (0.0 %) |
| ROLLUP (solo ordenamiento: fuera de AP-07 en MySQL) | 3 | 3 | 0 (0.0 %) |
| UNION / EXCEPT | 12 | 12 | 12 (100.0 %) |
| agregado ordenado (solo ordenamiento: fuera de AP-07 en MySQL) | 3 | 3 | 0 (0.0 %) |
| subconsulta IN | 5 | 5 | 5 (100.0 %) |
| ventana | 18 | 18 | 18 (100.0 %) |

## Controles

| Categoría | Casos | Marcados | Falsos positivos |
|---|---|---|---|
| DISTINCT chico | 6 | 0 | 0 |
| GROUP BY chico | 11 | 0 | 0 |
| conteo | 6 | 0 | 0 |
| filtro + GROUP BY | 6 | 0 | 0 |
| orden por indice | 5 | 0 | 0 |
| top-N | 6 | 0 | 0 |
