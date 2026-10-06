# Cómo se mide el sobrecosto del pipeline (`measure_overhead.py`)

Explicación honesta de qué hace cada fase, cómo mide, y cómo leer los resultados.

**Objetivo del informe** (`PrimerInforme.md:47,136`): el sobrecosto de recolección debe ser **inferior al 5 %** sobre la métrica de rendimiento de la carga observada.

**Métrica del veredicto:** **latencia p95** de las queries del load observado, con y sin el pipeline activo, bajo la misma carga continua.

```
sobrecosto_p95 = (p95(con pipeline) − p95(carga sola)) / p95(carga sola) × 100

CUMPLE si sobrecosto_p95 < 5
```

Es la pregunta directa del informe: **¿la carga observada se frenó?**

**No hay valores fijos ni resultado garantizado.** El script mide lo que mide:
- Si la carga se frenó ≥5% → `NO CUMPLE` (exit 2).
- Si no hay datos suficientes → `SIN_DATO` (exit 1), **no** se asume CUMPLE.
- Un sobrecosto ≤0 significa que en esa ventana la carga no se midió más lenta (puede ser ruido); no es "trabajo negativo".

**Métricas secundarias** (evidencia, no definen el veredicto): QPS del load, CPU/mem del contenedor, tiempo de servidor del monitor.

---

## Cómo se ejecuta

```bash
# Sandbox arriba (PG + MySQL + sysbench + cola PGMQ)
docker compose -f ql_sandbox/docker-compose.yml up -d
docker compose up -d

cd telemetry_pipeline
python measure_overhead.py 10 quick      # perfil rápido (~2-3 min)
python measure_overhead.py 20            # 20s por ventana, 3 rondas, pipeline cada 10s
python measure_overhead.py 20 3 10       # measure_s, rondas, cadencia (s)
```

**Aislamiento por motor:** medir postgres NO corre el pipeline de mysql (ni su load). Se usa `main.run_engine(dialect)`, la misma pieza que usa `main()`, pero solo para el motor medido.

| Exit code | Significado |
|-----------|-------------|
| `0` | CUMPLE (sobrecosto p95 < 5% en ambos motores) |
| `1` | Error o SIN_DATO (sandbox caído / sin queries OK en la ventana) |
| `2` | NO CUMPLE (sobrecosto p95 ≥ 5% en al menos un motor) |

---

## Qué mide y con qué

### Latencia y QPS de la carga (la métrica del veredicto)

`continuous_load.sh` corre while-true en `ql_sysbench` y **tiena cada query real** del load (las mismas 10 queries variadas que `battery.sh` contra `sbtest1`). Cada query escribe una fila en `/tmp/ql_load_metrics.csv` (dentro del contenedor):

```
ts_ms,engine,duration_ms,status
<epoch_ms>,postgres,<duración_ms>,0
```

- `duration_ms` = tiempo real de la query (incluye connect del cliente).
- `status=0` = query OK; otro valor = error del cliente.
- Antes de cada ventana se limpia el CSV; al final se leen solo las filas de ese motor.

De ahí se calculan, solo con queries OK:

| Campo | Fórmula |
|-------|---------|
| `p50_ms` | percentil 50 de las duraciones |
| `p95_ms` | percentil 95 de las duraciones |
| `qps` | queries OK / duración de la ventana |

Si una ventana tiene `< 20` queries OK se imprime un aviso: el p95 es poco confiable con tan pocas muestras.

### CPU y memoria (secundario)

Se leen de **Docker** cada 1.5 s (`docker stats`). Evidencia de recursos; **no** definen el veredicto (con CPU saturado el delta se pierde en ruido de muestreo).

### Tiempo de servidor del monitor (fase B, secundario)

Mide cuánto SQL del propio monitor ejecutó la DB durante **una** extracción por motor:

| Motor | Fuente | Filtro |
|-------|--------|--------|
| PostgreSQL | `pg_stat_statements` | rol `querylens_monitor` |
| MySQL | `performance_schema` | prefijos de `PIPELINE_FINGERPRINT` |

### La carga observada

`ql_sandbox/scripts/continuous_load.sh` con `ENGINE` = `postgres`, `mysql` o `both`. En la medición se usa **solo el motor medido**. **El load no se detiene** durante las ventanas.

---

## Fases del script

### `[A]` Base ociosa

- Duración: `WARMUP_S` (8 s).
- Qué hace: muestrea CPU/mem de ambos contenedores **sin carga ni pipeline**.
- Para qué sirve: referencia y sanity check del sandbox.

### `[B]` Costo absoluto de 1 extracción POR MOTOR (sin load)

- Qué hace, **una vez por motor**:
  1. Snapshots del monitor PG/MySQL.
  2. Corre `main.run_engine(dialect)` (solo ese motor).
  3. Snapshots → delta de statements y tiempo de servidor.
  4. CPU/mem del contenedor de ese motor durante la corrida.
- Para qué sirve: costo absoluto del pipeline de cada motor sin competencia.
- El monitor del motor **no** medido debe quedar en ~0: si sube, algo se coló (aislamiento roto).

### `[C]` Sobrecosto bajo carga continua (define el veredicto)

Por cada motor, de forma **aislada**:

1. **Load solo de ese motor** — `continuous_load.sh postgres` o `mysql`.
2. **Ventana A (carga sola)** — `MEASURE_S` segundos sin pipeline. Se limpia el CSV, se mide, se leen p50/p95/QPS + CPU/mem.
3. **Ventana B (carga + pipeline)** — misma ventana, load sigue. Pipeline cada `EXTRACT_EVERY_S`; si cadencia ≥ ventana, **1 disparo a la mitad** (`measure_s/2`) para que entre en la ventana de medición. Se leen p50/p95/QPS + CPU/mem de nuevo.
4. **Deltas por ronda:**
   - `sobrecosto_p95 = (p95_B − p95_A) / p95_A × 100` ← **métrica del veredicto**
   - `sobrecosto_qps = (qps_A − qps_B) / qps_A × 100` (secundario)
   - `cpu_overhead = cpu_B − cpu_A` en pts% (secundario)
5. **Stop load** y pasar al siguiente motor.

Con `10 quick`: `measure_s=10`, `rounds=2`, `extract_every_s=10` → 1 extracción en t≈5 s.

El **veredicto del motor** es `verdict(promedio de sobrecosto_p95 de las rondas)`:
- Si alguna ronda no dio p95, queda fuera del promedio.
- Si **ninguna** dio p95 → `SIN_DATO` (no se rescata con QPS ni CPU).
- QPS y CPU se reportan siempre, pero no "rescatan" el veredicto.

El **global** exige ambos motores (`overall_verdict`).

---

## Cómo leer la salida

```
RESUMEN (veredicto = sobrecosto p95 latencia de la carga)
  postgres   p95 <A>ms -> <B>ms sobrecosto=<X>% | qps=<Y>% | cpu=<Z> pts% rondas=<N> -> <VEREDICTO>
  mysql      ...
  fase B postgres: pipeline=...s pg_monitor=...s mysql_monitor=...s
  fase B mysql: ...
  umbral p95 < 5% | GLOBAL: <VEREDICTO>
```

| Campo | Significado |
|-------|-------------|
| `p95 A -> B` | Percentil 95 de latencia del load, sola vs con pipeline |
| `sobrecosto=X%` | Cuánto se frenó la carga (p95). Veredicto si es < 5% |
| `qps=Y%` | Caída de throughput del load (secundario) |
| `cpu=Z pts%` | Delta de CPU del contenedor (secundario) |
| `GLOBAL` | CUMPLE solo si ambos motores CUMPLE |

**Sobrecosto ≤ 0:** en esa medición la carga no se midió más lenta. Puede ser ruido (ventanas cortas, pocas queries). No prueba que el pipeline sea gratis; prueba que **no se midió frenado ≥5%**.

**Sobrecosto ≥ 5%:** la carga se frenó en ≥5% con pipeline → `NO CUMPLE`.

**SIN_DATO:** no hubo queries OK suficientes o el sandbox falló. Hay que reintentar con ventana más larga o sandbox arriba.

---

## El JSON de evidencia

Queda en `telemetry_pipeline/logs/overhead_<timestamp>.json` (gitignored). Es la fuente de verdad de cada run; los números de la consola salen de ahí.

```json
{
  "method": "continuous_load_per_engine_latency",
  "metric": "latency_p95_pct",
  "umbral_pct": 5.0,
  "verdict": "CUMPLE | NO CUMPLE | SIN_DATO",
  "idle": { "postgres": {"cpu_pct": 0.0}, "mysql": {} },
  "extraction": {
    "postgres": { "wall_s": 0.0, "postgres": {"monitor_server_s": 0.0}, "mysql": {} },
    "mysql":    { "wall_s": 0.0, "postgres": {"monitor_server_s": 0.0}, "mysql": {} }
  },
  "engines": {
    "postgres": {
      "metric": "latency_p95_pct",
      "p95_load_ms": null,
      "p95_con_ms": null,
      "p95_overhead_pct": null,
      "qps_load": null, "qps_con": null, "qps_overhead_pct": null,
      "cpu_load_pct": null, "cpu_con_pct": null, "cpu_overhead_pct": null,
      "verdict": "SIN_DATO",
      "round_details": [
        { "round": 1,
          "latency_load": {"n": 0, "failures": 0, "p50_ms": null, "p95_ms": null, "qps": null},
          "latency_con":  {"n": 0, "failures": 0, "p50_ms": null, "p95_ms": null, "qps": null},
          "p95_overhead_pct": null, "qps_overhead_pct": null,
          "cpu_overhead_pct": null, "extractions": 0 }
      ]
    },
    "mysql": { "...": "mismo shape" }
  }
}
```

| Sección | Para qué sirve |
|---------|----------------|
| `idle` | Base ociosa (fase A) |
| `extraction.<motor>` | Costo absoluto + monitor (fase B, por motor) |
| `engines.*.p95_overhead_pct` | **El delta que define el veredicto** |
| `engines.*.qps_overhead_pct` | Throughput del load (secundario) |
| `engines.*.cpu_overhead_pct` | CPU del contenedor (secundario) |
| `engines.*.round_details` | p50/p95/QPS/n/failures por ronda |

Los campos van en `null` cuando no hay dato; el script **no** rellena con ceros ni inventa percentiles.

---

## Ejemplo de un run real

Números de un `python measure_overhead.py 10 quick` en el sandbox del repo (JSON `logs/overhead_20261006_125958.json`). **Varían entre runs** (ruido de Docker, carga de la máquina host, cuántas queries entran en la ventana). No los uses como referencia de "lo que debería dar": usá el JSON de tu propio run.

| Motor | p95 sola | p95 con pipeline | Sobrecosto p95 | QPS | CPU | Veredicto |
|-------|----------|------------------|----------------|-----|-----|-----------|
| postgres | 506.5 ms | 522.1 ms | +3.1% | +5.1% | +3.9 pts% | CUMPLE |
| mysql | 227.4 ms | 225.6 ms | −0.8% | −1.0% | +2.5 pts% | CUMPLE |

Rondas de postgres en ese run (muestran el ruido real):

| Ronda | p95 sola | p95 con pipeline | Sobrecosto p95 | n (sola/con) |
|-------|----------|------------------|----------------|--------------|
| 1 | 507.7 ms | 489.8 ms | −3.5% | 87 / 86 |
| 2 | 505.3 ms | 554.4 ms | **+9.7%** | 88 / 80 |

- El promedio de las rondas fue +3.1% (CUMPLE) aunque la ronda 2 sola superó 5%. El veredicto es el promedio, no el peor pico de una ventana de 10 s.
- En postgres el sobrecosto QPS medio quedó en +5.1%: por eso QPS **no** define el veredicto (el informe pide la métrica de rendimiento elegida, y esa es p95 de latencia).
- ~87 queries OK por ventana (postgres), ~144 (mysql) en ese run.
- Fase B: postgres 0.6 s (pg_monitor 0.284 s); mysql 0.4 s (mysql_monitor 0.033 s). CPU de fase B fue `n/a` (pipeline < intervalo del sampler de Docker).
- GLOBAL: CUMPLE en **ese** run. Otros runs pueden dar valores distintos, incluso negativos, NO CUMPLE o SIN_DATO.

---

## Limitaciones conocidas

1. **Ventanas cortas = pocos samples.** Con 10 s puede haber pocas queries OK; el p95 se estabiliza con 20–30 s y 3+ rondas. El script avisa si `n < 20`.
2. **Sobrecosto ≤0 no es "gratis".** Significa que en esa ventana no se midió frenado ≥5%. Con ruido puede quedar en 0 o negativo.
3. **CPU como proxy es débil** con contenedor saturado: por eso el veredicto usa latencia de la carga, no CPU.
4. **Fase B CPU suele ir `n/a`:** el sampler de Docker es cada 1.5 s y la extracción dura ~0.3–0.6 s; a menudo no cae sample en la ventana. El wall y el monitor del servidor sí se reportan.
5. **1 extracción por ventana** (con cadencia ≥ ventana) puede ser poco presión para el pipeline. Para un stress real, bajá la cadencia (ej. `20 3 5` = ventana 20 s, pipeline cada 5 s).
6. **MySQL sin digest poblado.** Si `performance_schema` no tiene stats del fingerprint, el tiempo del monitor va `null` (solo afecta fase B).
7. **No corre en CI.** Requiere Docker con `ql_sysbench`. CI solo valida que las extracciones se encolan.
8. **`scripts/` está montado** (`./scripts:/scripts`), cambios en `continuous_load.sh` no requieren rebuild.

---

## Flujo completo en una imagen

```
sandbox up
    │
    ├─ [A] muestrear CPU/mem ocioso (8s)
    │
    ├─ [B] 1 extracción POR MOTOR (sin load)
    │       snapshots → run_engine(dialect) → snapshots
    │
    ├─ por motor (aislado):
    │   ├─ start_load(engine)
    │   ├─ warmup 8s
    │   ├─ N rondas:
    │   │   ├─ limpiar CSV
    │   │   ├─ ventana MEASURE_S con load SOLO
    │   │   │   → leer CSV → p50/p95/QPS + CPU
    │   │   ├─ limpiar CSV
    │   │   ├─ ventana MEASURE_S con load + run_engine cada N s
    │   │   │   (1 disparo al medio si cadencia >= ventana)
    │   │   │   → leer CSV → p50/p95/QPS + CPU
    │   │   └─ sobrecosto_p95 = (p95_B - p95_A) / p95_A * 100
    │   └─ stop_load()
    │
    └─ veredicto global (promedio p95 < 5%, sin datos => SIN_DATO) + JSON
```
