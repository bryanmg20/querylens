# Sobrecosto del pipeline v2 (`overhead/`)

Medición vigente del requisito del informe (`PrimerInforme.md`): **el sobrecosto de recolección debe ser inferior al 5 %** sobre la métrica de rendimiento de la carga observada. Reemplaza el método de [`MEASURE_OVERHEAD.md`](MEASURE_OVERHEAD.md) (`measure_overhead.py`), que queda sin cambios como medición anterior.

## Por qué un método nuevo

| Problema de `measure_overhead.py` | Qué hace v2 |
|---|---|
| La carga abre un `psql`/`mysql` por query: la latencia incluye fork + TCP + auth | `overhead/loadgen.py`: conexiones persistentes, mide al servidor |
| Siempre corre A y después B: la deriva (caché, autovacuum, log que crece) cae sobre B | Pares de bloques en orden aleatorio (AB o BA), y los pares de todos los brazos mezclados; semilla en el JSON |
| p95 de todas las queries juntas: la mezcla de 10 queries con costos 10x distintos mueve el número | p95 **por plantilla**, combinado con media geométrica |
| Promedio de 2-5 rondas sin intervalo de confianza | IC95 por bootstrap de pares y veredicto sobre el IC |
| Nadie mide el ruido del host | Brazo A/A (carga contra carga) |
| Nadie prueba que el arnés detecta un costo | Control positivo (`--positive-control`) |
| Postgres: auto_explain activo en las dos ventanas, su costo nunca se mide | Brazo de instrumentación, reportado aparte |
| MySQL: costo del monitor con `LIKE` sobre `PIPELINE_FINGERPRINT` | `events_statements_summary_by_user_by_event_name` del usuario `querylens_monitor` |
| `run_engine` suelto dentro del proceso del medidor | `overhead/pipeline_proc.py`: daemon en otro proceso con `runner.Runner` y cadencia real |
| El contenedor `querylens_pipeline` del compose extrae en paralelo y ensucia la ventana "carga sola" | El preflight aborta si está corriendo |

## Cómo correrlo

```bash
# 1. Sandbox y cola arriba
docker compose -f ql_sandbox/docker-compose.yml up -d
docker compose up -d

# 2. Parar el daemon del compose: si extrae, ensucia los bloques sin pipeline
docker stop querylens_pipeline

# 3. Medir (desde telemetry_pipeline/, con el venv)
cd telemetry_pipeline
python -m overhead.run --profile quick                                 # ~10 min: el arnés funciona (NO es evidencia)
python -m overhead.run --profile quick --positive-control              # el arnés detecta un costo conocido
python -m overhead.run --profile serious --engine both                 # ~1 h: evidencia del informe
python -m overhead.run --profile serious --engine postgres             # ~30 min, un motor

# 4. Volver a levantar el daemon
docker start querylens_pipeline
```

| Perfil | Bloque | Pares por motor | Tiempo aprox. |
|---|---|---|---|
| `quick` | 25 s | A/A 2 + pipeline 3 | ~5 min por motor |
| `serious` | 45 s | A/A 3 + pipeline 10 + instrumentación 6 | ~30 min por motor |
| `--stress` | igual | +4 pares con cadencia de 2 s | +6 min por motor |
| `--positive-control` | igual | +3/+4 pares | +3/+6 min por motor |

Opciones: `--pairs N` (pares del brazo pipeline), `--block-s S`, `--warmup-s S` (60 s), `--cadence S` (10 s por defecto, como `runner.DEFAULT_INTERVAL_S`), `--workers N` (2), `--seed`, `--skip-instrumentation`, `--allow-degraded` (medir sin auto_explain/funciones de log o con consumers apagados), `--allow-external-pipeline` (medir con `querylens_pipeline` arriba; solo para probar el código).

Si el veredicto sale `INCONCLUSO`, repetir con más pares (`--pairs 16`).

| Exit | Veredicto |
|---|---|
| `0` | CUMPLE |
| `1` | SIN_DATO o error (preflight, sandbox caído) |
| `2` | NO CUMPLE |
| `3` | INCONCLUSO |

## Qué corre

Tres procesos en el host. Todos usan `time.monotonic_ns` (CLOCK_MONOTONIC, el mismo reloj para todos), y con eso el controlador corta las muestras por bloque.

```
overhead.run (controlador)
 ├─ overhead.loadgen        carga: N workers, conexión persistente, lazo cerrado
 │                          -> <motor>_load.csv  (t0_ns,t1_ns,plantilla,ok)
 ├─ overhead.pipeline_proc  pipeline real (Runner.cycle -> main.run_engine), on/off por stdin
 │                          -> <motor>_cycles.jsonl (inicio/fin de cada ciclo, candidatos,
 │                             planes de auto_explain, bytes de log leídos)
 └─ snapshots en cada borde de bloque: cgroup cpu.stat del contenedor y costo del monitor
```

- **Carga**: las 10 plantillas de `continuous_load.sh` (`overhead/workload.py`). En Postgres corre como `ql_user` y en MySQL como `app_user`.
- **Pipeline**: el mismo código que el daemon de producción. Queda vivo entre bloques (preflight, imports y pools calientes). Al pasar a `off` espera que termine el ciclo en curso, así ningún ciclo pisa el bloque siguiente. Encola en PGMQ y escribe `pipeline_health` como siempre.

### Brazos (por motor)

| Brazo | Base | Tratamiento | Para qué |
|---|---|---|---|
| `aa` | carga | carga | Piso de ruido. Si su IC no cae dentro de ±5 %, el host no resuelve el umbral → aviso `RUIDO_EXCESIVO` |
| `pipeline` | carga | carga + pipeline cada `--cadence` s | **Veredicto** |
| `stress` | carga | carga + pipeline cada 2 s | Cota superior (opcional, no entra en el veredicto) |
| `positive_control` | carga | carga + 3 copias de una query de CPU pura (~1,5 s cada una) | El arnés tiene que detectarlo: `detectado=True` si la cota inferior de su IC es ≥ 5 % (lo habría declarado NO CUMPLE). Si no, la medición no sirve |
| `instrumentation` | carga, instrumentación apagada | carga, instrumentación como está | Costo de la configuración que QueryLens pide. **Se reporta aparte**, no entra en el 5 % |

Los pares de todos los brazos se **mezclan al azar** en una sola secuencia. Si corrieran brazo por brazo, un evento pasajero del host caería entero en un solo brazo. Pasó en la primera corrida seria: el primer checkpoint tras levantar Postgres cayó dentro del A/A, que corría primero. Antes del primer bloque hay `--warmup-s` (60 s) de carga sola; después de levantar el sandbox conviene subirlo (`--warmup-s 300`).

Cada bloque descarta sus primeros 5 s (`settle`). Una query que cruza el borde del bloque no cuenta en ninguno de los dos. En los bloques con pipeline, el primer ciclo arranca justo al terminar el `settle`, y desde ahí sigue la cadencia.

### Instrumentación: qué se apaga

Solo lo que el pipeline consume:

- **Postgres**: `auto_explain.log_min_duration` = `-1` para el rol de la carga (`ALTER ROLE ql_user SET`), y se terminan sus sesiones para que reconecten con el valor nuevo. No sirve `ALTER SYSTEM`: el `-c` del compose le gana a `postgresql.auto.conf`. La reconexión se hace en los dos sentidos, así pesa igual en ambos lados.
- **MySQL**: consumers `statements_digest` y `events_statements_current`. Los demás que activa el sandbox (`events_statements_history_long`, `events_waits_*`) **no** los lee el pipeline y no se tocan. `performance_schema=ON` y los instrumentos quedan: apagarlos exige reiniciar.

Como los brazos están mezclados, después de cada bloque con la instrumentación apagada se vuelve enseguida al estado original, y el resto de los bloques corre con la configuración desplegada. Al terminar, también con Ctrl+C, se restaura el estado original y se verifica (`finally` + `atexit`). Para comprobarlo a mano:

```bash
docker exec ql_postgres psql -U ql_user -d ql_demo -tAc "SHOW auto_explain.log_min_duration; SELECT count(*) FROM pg_db_role_setting"
docker exec ql_mysql mysql -uroot -pql_root -e "SELECT NAME, ENABLED FROM performance_schema.setup_consumers"
```

## Métrica y veredicto

Por brazo, con las muestras OK de todos sus pares:

1. Por plantilla `t`: `r_t = p95(tratamiento) / p95(base)`.
2. `sobrecosto = (Π r_t)^(1/T) − 1`, en %. Cada plantilla pesa igual.
3. IC95 por **bootstrap de pares** (2000 remuestreos de pares completos). Respeta la deriva entre pares.

| Resultado | Condición |
|---|---|
| `CUMPLE` | cota **superior** del IC < 5 % |
| `NO CUMPLE` | cota **inferior** del IC ≥ 5 % |
| `INCONCLUSO` | el IC cruza el 5 % |
| `SIN_DATO` | alguna plantilla con < 50 muestras en algún lado, o < 2 pares |

Es más exigente que el método viejo: "media < 5 %" ya no alcanza, tiene que quedar bajo el umbral la banda entera.

**Secundarias** (no deciden):
- Latencia media y throughput (queries OK/s), con el mismo esquema.
- `durante_extraccion`: latencia de las queries que se solapan con un ciclo, contra las que no. Es una cota del peor caso.
- `duty`: tiempo de ciclo sobre tiempo medido.
- CPU del contenedor, en % de un core, desde `usage_usec` de cgroup v2.
- Costo del monitor por segundo: llamadas, `exec_ms` y bloques en PG; filas examinadas en MySQL.
- Solo PG: bytes por segundo que crece el log JSON.

## Salida

Cada run deja `logs/overhead_v2_<timestamp>/` (gitignored):

| Archivo | Contenido |
|---|---|
| `result.json` | Fuente de verdad: configuración, semilla, brazos, IC, ciclos, `pipeline_health`, avisos, y los bordes de cada bloque (`blocks`), para re-analizar con los CSV sin volver a medir |
| `<motor>_load.csv` | Cada query de la carga |
| `<motor>_cycles.jsonl` | Cada ciclo del pipeline o del control positivo |
| `<motor>_pipeline.log`, `_loadgen.log`, ... | stderr de los subprocesos |

Resumen en consola:

```
postgres: CUMPLE  (muestras=...)
  aa                p95=+0.4% IC=[-2.1, +2.8] ... -> ruido_ok=True
  pipeline          p95=+1.2% IC=[-0.9, +3.6] ... -> CUMPLE
                    ciclos=40 fallidos=0 wall=0.5s duty=5.0% durante_extraccion(media)=+8.0%
  instrumentation   p95=+0.8% IC=[-1.5, +3.0] ... -> CUMPLE
```

(Formato ilustrativo, no un resultado.)

## Cómo confiar en el número

Antes de citar un veredicto:

1. `ruido_ok=True` en el A/A de cada motor.
2. Sin avisos de ciclos fallidos ni de pocas muestras. `pipeline_health.last_run.status = ok`.
3. En PG, los ciclos muestran `auto_explain_plans_mean > 0` y `log_bytes_read_mean > 0`: el ciclo medido es el pipeline actual, no uno degradado al plan genérico.
4. Al menos una vez en ese host, `--positive-control` con `detectado=True`.
5. Los candidatos son la carga observada y no artefactos. Una query ejecutada a mano en `psql` queda en `pg_stat_statements` y el pipeline la toma como candidata. Si su `EXPLAIN (GENERIC_PLAN)` falla, `pipeline_health` queda `degraded` con `EXPLAIN_FAILED`. Se borra solo esa entrada con `SELECT pg_stat_statements_reset(0, 0, <queryid>)`.

Qué reportar en el informe: el comando exacto, la semilla, por motor el p95 del brazo `pipeline` con su IC y veredicto, el A/A, la instrumentación (aparte) y `result.json` como evidencia.

## Limitaciones

1. **El host es chico.** WSL con 4 CPU y 2 GB: carga, pipeline, contenedores y el resto del compose compiten. Con 2 workers, Postgres ya usa ~2,2 cores. Por eso el A/A es obligatorio.
2. **Es el sandbox, no producción.** `sbtest1` tiene 10 000 filas y `pg_stat_statements` tiene pocas entradas. El costo de `STATEMENTS_QUERY` crece con las entradas, y la lectura de log con el volumen de auto_explain (hasta `LOG_WINDOW_BYTES` = 2 MB por ciclo).
3. **El pipeline corre en el host**, con la misma CPU que el servidor (Docker en WSL). En producción suele ir en otra máquina, así que acá el costo queda sobrestimado.
4. **Atribución en PG**: con `pg_stat_statements.track=all`, las sentencias anidadas dentro de `querylens.read_log()` (`SECURITY DEFINER`) se atribuyen al dueño de la función, no a `querylens_monitor`. El tiempo de la llamada de nivel superior sí cuenta.
5. **El brazo de instrumentación** no apaga `performance_schema` ni `shared_preload_libraries`: exigen reiniciar. Mide el costo de los consumers y de auto_explain, no el de tener la librería cargada.
6. **No corre en CI**: necesita Docker y el sandbox. CI cubre `tests/test_overhead_stats.py` (unit).
