# Banco de pruebas de un solo comando

Ejecuta la validación del objetivo 7 del `SegundoInforme`: los **22 escenarios de inyección** con **10 repeticiones** cada uno y **10 corridas de control de 30 min por motor**, sin supervisión. Después calcula automáticamente las métricas **M1 a M4** de la Tabla 7 contra los manifiestos de cada escenario.

```bash
# desde la raíz del repo, con el sandbox y el compose de la raíz levantados
python ql_sandbox/banco/banco.py                      # campaña completa (unas 18 h)
python ql_sandbox/banco/banco.py --smoke              # 1 repetición y 1 control de 2 min (~1 h)
python ql_sandbox/banco/banco.py --reanudar <corrida> # sigue una corrida cortada
python ql_sandbox/banco/metricas.py ql_sandbox/banco/resultados/<corrida>   # recalcula sin correr nada
```

Solo usa la librería estándar de Python. Requisitos:

- El sandbox levantado: `docker start ql_postgres ql_mysql ql_sysbench`.
- El compose de la raíz levantado, con las imágenes al día: `docker compose up -d --build`.

## Qué corre

Todo pasa por el flujo real, como en la base de un cliente:

1. La base del sandbox se registra con `querylens add`.
2. El servicio `pipeline` recolecta cada 10 s.
3. El servicio `detection-engine` procesa la cola y los hallazgos quedan en `public.hallazgos`.

El banco no toma instantáneas por su cuenta. Solo inyecta y lee lo que QueryLens guardó.

**Carga base.** `scripts/base_load.sh` corre sysbench `oltp_read_write` con 4 hilos a una tasa fija (50 tx/s por defecto, `--tasa`). Corre todo el tiempo en el motor que se mide. Las inyecciones se hacen sobre ella, y las corridas de control son la carga base sola.

**Por motor, en este orden:**

| Paso | Qué es | Fuente | Duración aprox. por repetición |
|---|---|---|---|
| 1 | 6 escenarios de antipatrones | tabla y carga de `scenarios/run_scenario.py` | 1–3 min |
| 2 | Bloque de contención | casos `positivo` de `mediciones/measure_ct.py` | ~30 s por caso |
| 3 | Escenario de AP-01 degradación respecto a la línea base | `run_scenario.py` | ~12 min |
| 4 | Corridas de control | carga base sola | 30 min |

El escenario de AP-05 índice no utilizado o redundante espera, si hace falta, a que el motor lleve 30 min encendido: antes de eso "no utilizado" no se evalúa.

**Escenarios.** Son 22 para 24 combinaciones patología-motor:

- **Antipatrones:** 7 por motor. AP-02 escaneo completo evitable se evalúa dentro del escenario de AP-04 índice ausente.
- **Contención en PostgreSQL:** CT-01, CT-02 y CT-05.
- **Contención en MySQL:** CT-01 a CT-05.

**Una repetición de un escenario de antipatrones:**

1. Prepara la tabla.
2. Espera un ciclo del pipeline como corte.
3. Arranca la carga del escenario. El inicio de la inyección es ese momento. En AP-01 es el momento en que se elimina el índice, después de 63 ciclos de línea base.
4. Observa entre 30 s y 180 s: termina antes si ya salieron todos los esperados y pasaron los 30 s mínimos.
5. Detiene la carga y borra la tabla.

## Manifiestos (`manifiestos.py`)

Cada escenario declara lo que QueryLens tiene que encontrar, que es la verdad por construcción:

- **Esperados:** si aparecen, VP; si no, FN.
- **Colaterales:** hallazgos de otra patología que la misma inyección produce por construcción. Por ejemplo, en el escenario de AP-01 degradación respecto a la línea base, al eliminar el índice la consulta pasa a recorrer toda la tabla, y eso es AP-02 escaneo completo evitable y AP-04 índice ausente. Son ciertos, pero no son el objetivo del escenario: no cuentan como VP ni como FP.
- **`LINEA_BASE`:** hallazgos propios de la carga base, por motor. Tampoco cuentan como FP (informe, sección 3.3).

Todo hallazgo que no coincide con ninguno de los tres es un **falso positivo** de su patología, en cualquier corrida.

## Métricas (`metricas.py`)

| Métrica | Cómo se cuenta | Umbral |
|---|---|---|
| M1 exhaustividad | VP / (VP + FN) por combinación. Cada repetición es una inyección. Una patología con varios esperados (AP-05 tiene 3, AP-06 tiene 2) es VP solo si salen todos. | ≥ 0,90 |
| M2 precisión | VP / (VP + FP) por combinación. Un problema que se detecta en varias ventanas de la misma corrida cuenta una vez. | ≥ 0,90 |
| M3 contención | De los ciclos o cadenas detectados: proporción con transacciones y recursos correctos, y proporción con el patrón correcto. | ≥ 0,95 y ≥ 0,90 |
| M4 latencia | Desde el inicio de la inyección hasta que el hallazgo queda guardado (`detectado_en`). Se informa la mediana y el máximo, en segundos y en ventanas de 10 s. | Antipatrones ≤ 120 s; CT-01 a CT-04 ≤ 15 s; CT-05 ≤ 45 s desde que la raíz cumple 30 s |

**M4 en los antipatrones.** El informe dice "≤ 2 ventanas (120 s)", porque se escribió con ventanas de 60 s. Se mantiene el límite en segundos, y la latencia también se reporta en ventanas.

M5 (sobrecosto) se mide con `telemetry_pipeline/overhead`. M6 (anonimización) se mide con `scenarios/check_literal_leak.py`. M7 (completitud) es la lista de chequeo sobre los hallazgos de la campaña: explicación, recomendación y riesgo de cada problema, y la evidencia y el enlace con la instantánea (`snapshot_msg_id` en `pgmq.a_analyze_job`) de cada detección.

## Salida (`resultados/<corrida>/`, no se versiona)

| Archivo | Contenido |
|---|---|
| `meta.json` | Parámetros, commit y versiones de los motores. |
| `corridas.jsonl` | Una línea por corrida terminada, con sus hallazgos. Es lo que lee `--reanudar`. |
| `metricas.json` | M1 a M4 por combinación, FN, FP y declarados. |
| `reporte.md` | Lo mismo, para leer. Se actualiza al terminar cada corrida. |
| `errores.log` | Corridas que fallaron. Cada una se intenta 2 veces. Si fallan 3 seguidas, se deja el resto del motor para `--reanudar`. |

**Código de salida:**

| Código | Significado |
|---|---|
| 0 | M1 a M4 cumplen en las 24 combinaciones. |
| 2 | Alguna métrica no cumple. |
| 1 | Quedaron corridas sin terminar. |

## Calibración y campaña final

Los umbrales se calibran con corridas distintas de las de validación, por ejemplo `--etiqueta calibracion`. Después se congelan en `detection_engine/umbrales_congelados.json`; la prueba `test_umbrales_congelados.py` se cae si alguno cambia sin actualizar ese archivo. La campaña final se corre con los umbrales congelados, y si alguno cambia después, se repite completa.
