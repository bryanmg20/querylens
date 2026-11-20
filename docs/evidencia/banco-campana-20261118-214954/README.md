# Campaña final de validación (#62)

Corrida `banco-campana-20261118-214954` del banco de pruebas (`ql_sandbox/banco`), con los umbrales congelados (`detection_engine/umbrales_congelados.json`, versión 2).

| | |
|---|---|
| Commit | `d1cf211` (árbol limpio) |
| Inicio / fin | 18 de noviembre de 2026, 21:49 → 19 de noviembre de 2026, 14:57 (hora de Colombia; ~17 h) |
| Comando | `python ql_sandbox/banco/banco.py --engine both --reiniciar-sentencias --etiqueta campana` |
| Motores | MySQL 8.0.46 y después PostgreSQL 17.11 |
| Plan por motor | 7 escenarios de anti-patrones × 10 repeticiones, bloque de contención (10 repeticiones por caso) y 10 corridas de control de 30 min |
| Carga base | sysbench `oltp_read_write`, 4 hilos, 50 tx/s |

## Resultado

| Métrica | Resultado | Veredicto |
|---|---|---|
| M1 exhaustividad | 24/24 combinaciones (0 falsos negativos) | CUMPLE |
| M2 precisión | 24/24 combinaciones (0 falsos positivos, también en los 20 controles) | CUMPLE |
| M3 contención | 8/8 combinaciones (miembros y patrón 10/10 en cada una) | CUMPLE |
| M4 latencia | 24/24 combinaciones | CUMPLE |
| M7 completitud | 1174/1174 problemas | CUMPLE |

El detalle por combinación está en `reporte.md`.

## Archivos

| Archivo | Contenido |
|---|---|
| `reporte.md` | Reporte del banco: veredicto de la Tabla 7, métricas por combinación, M3, M7, falsos negativos, falsos positivos y hallazgos declarados en los manifiestos |
| `metricas.json` | Las mismas métricas en JSON |
| `meta.json` | Parámetros de la corrida y versiones de los motores |
| `corridas.jsonl` | Una línea por corrida (162): inicio, fin y hallazgos leídos de `public.hallazgos` |
| `campana.log` | Salida del banco durante la corrida |
| `ct-mysql-20261118-232208/`, `ct-postgres-20261119-074239/` | Bloque de contención de cada motor (`mediciones/measure_ct.py`): reporte, resultados y verdad declarada por el script de inyección |

## Recalcular

`reporte.md` y `metricas.json` se recalculan desde `corridas.jsonl` y `meta.json`, sin volver a correr nada:

```
python ql_sandbox/banco/metricas.py docs/evidencia/banco-campana-20261118-214954
```

Se comprobó que el resultado es idéntico a los archivos de esta carpeta.
