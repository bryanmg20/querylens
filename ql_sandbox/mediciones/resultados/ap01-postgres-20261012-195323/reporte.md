# AP-01 degradacion respecto de la linea base — postgres

Corrida `ap01-postgres-20261012-195323` · db_id `medicion-ap01-postgres-20261012-195323` · 14 snapshots cada 60 s · inicio 2026-10-13 00:53:24.569332+00

## Resultado

| Métrica | Valor |
|---|---|
| Positivos detectados | **100/100 (100.0 %)** · IC 95 % 96.3–100.0 % |
| ¿Cumple ≥ 90 %? | sí |
| Positivos donde la patología se verificó en los contadores | 100/100 |
| Detección entre los verificados | 100/100 (100.0 %) |
| Falsos positivos (controles marcados sin la patología) | 0/40 (0.0 %) |
| Controles marcados en total | 0/40 |
| Marcados antes de degradar | 0 |
| Detectados después de 2 ventanas | 0 |
| Controles marcados que sí se degradaron (contención de la inyección) | 0 |
| Ventana de detección (1 = la primera tras degradar) | 1: 100 |

## Por categoría

| Categoría | Casos | Patología verificada | Detectados |
|---|---|---|---|
| crecimiento de datos | 25 | 25 | 25 (100.0 %) |
| indice eliminado | 50 | 50 | 50 (100.0 %) |
| indice menos selectivo | 25 | 25 | 25 (100.0 %) |

## Controles

| Categoría | Casos | Marcados | Falsos positivos |
|---|---|---|---|
| control en tabla degradada | 12 | 0 | 0 |
| control en tabla sin cambios | 28 | 0 | 0 |
