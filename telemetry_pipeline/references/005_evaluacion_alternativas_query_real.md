# 9. Evaluación de alternativas — Recuperación de la query real

> Documento de evaluación (sección 9 del informe) de las dos alternativas implementadas
> para recuperar el texto real de una consulta candidata antes de ejecutar `EXPLAIN`:
> **Opción A (tabla de actividad)** y **Opción B (logs del servidor)**.
> Incluye dos pruebas: la comparativa directa A vs B (`compare_real_query_mechanics.py`)
> y la ablación de la ventana de logs de la Opción B (`ablate_recovery_window.py`).

---

## 9.1 Resultado medido (evidencia)

El harness compara ambas opciones sobre el mismo banco de pruebas y los mismos snapshots:
corre la batería (`battery.sh` con ruido de tráfico barato), ejecuta el pipeline, lee los
últimos snapshots de PGMQ y clasifica cada candidato de `top_impact_queries` según qué
opción recuperó (o no) la query real.

| Motor | Candidatos | Opción A (actividad) | Opción B (logs) |
|-------|-----------|----------------------|-----------------|
| PostgreSQL | 30 | **0 (0 %)** | **30 (100 %)** |
| MySQL | 30 | **0 (0 %)** | **30 (100 %)** |

Condiciones de la corrida: 3 muestras × 5 repeticiones por query, 50 queries de ruido
posterior por motor (el log sigue creciendo con tráfico que no entra al top-impact), 10
top-impact por motor por muestra. La suite de tests del pipeline sigue en verde (118 tests).

### 9.1.1 Prueba complementaria de ablación — límites de la ventana de logs

El 100 % de la Opción B no es gratuito: depende de que la cola del log que se lee
(`LOG_TAIL_LINES`, por defecto 200 000 líneas físicas) alcance a cubrir el volumen de
tráfico ejecutado antes del snapshot. Para medir cuándo la Opción B **empieza a perder**,
se ejecutó una ablación (segunda prueba, `ablate_recovery_window.py`) variando dos
factores: el tamaño de la ventana de cola del log y el tráfico de relleno (ruido) escrito
**después** de la batería heavy — que es lo que entierra las top-impact más atrás en el log.

Condiciones: 2 muestras por celda, batería de 3 repeticiones por query (10 queries = 30
bloques por motor), grid de 6 celdas.

| Ventana de log (líneas) | Ruido posterior | MySQL | PostgreSQL |
|-------------------------|-----------------|-------|------------|
| 200 000 | 0 | 100 % | 100 % |
| 200 000 | 900 | 100 % | 100 % |
| 1 000 | 900 | **50 %** | **10 %** |
| 200 | 900 | **10 %** | **15 %** |
| 200 | 0 | **10 %** | 100 % |
| 50 | 0 | **0 %** | **0 %** |

Lectura de la ablación:

- **La ventana gigante absorbe el ruido**: con 200 000 líneas la recuperación sigue en
  100 % aunque el tráfico posterior llegue a ~940 entradas (900 de ruido + batería).
- **La pérdida ocurre cuando el tráfico excede la ventana**: con 1 000 líneas y 900 de
  ruido, la batería queda enterrada al inicio del log y fuera de la cola leída
  (PostgreSQL cae más rápido: el csvlog es 1 fila por query, así que 900 ruidos dejan la
  batería más lejos).
- **MySQL cae incluso sin ruido con ventana 200**: el slow log ocupa ~5 líneas físicas por
  bloque, por lo que 30 bloques ≈ 150+ líneas y una ventana de 200 corta el inicio de la
  batería; PostgreSQL con csvlog de 1 fila la recupera entera.
- **En todos los casos la pérdida se explica por `fuera`** (la firma de la top-impact ya no
  está dentro de la ventana leída), no por fallos del matching: cuando la entrada está en la
  ventana, la recuperación es determinista.

**Regla de producción derivada**: el valor de `LOG_TAIL_LINES` debe dimensionarse para
cubrir el volumen de entradas que el log puede producir en la ventana de recuperación bajo
carga pico (TPS × duración de la query más larga esperada + margen). Si el tráfico supera
la ventana, las top-impact más antiguas se pierden en silencio, el mismo modo de fallo que
se buscó evitar con la Opción B. Resultado reproducible con:

```
venv\Scripts\python.exe ablate_recovery_window.py [MUESTRAS] [REPS]
```

---

## 9.2 Explicación detallada de cada mecanismo

### Opción A — Tabla de actividad (versión antigua)

**Cómo funciona.** En la etapa de candidatos (`CandidatesStage`), el selector
`select_explain_ready` cruzaba el `query_id` de cada candidato contra la telemetría de
*actividad en vivo* recolectada en `active_queries`:

- PostgreSQL: `pg_stat_activity` (sesiones ejecutándose en ese instante).
- MySQL: `performance_schema.events_statements_current` (sentencias actualmente en curso).

Si el `query_id` del candidato aparecía en esa ventana de actividad, se marcaba
`real_query_found = True` y se reemplazaba el `query_text` por el texto de la query en
curso. Es la mecánica anterior al commit `c730da1` (`select_explain_ready` original,
visible en `git show 600dcfc:telemetry_pipeline/stages/selectors.py`).

**Por qué dio 0 %.** La tabla de actividad es una *fotografía puntual*: solo contiene lo
que está ejecutándose **en el instante exacto** en que el pipeline toma el snapshot. Las
queries de la batería son operaciones de agregación/join sobre tablas de ~10 000 filas
que terminan en milisegundos; al momento del muestreo ya terminaron, así que no hay nada
"en vuelo" que capturar. De hecho, en las corridas de validación `active_queries` traía
una sola fila por captura, y esa fila no era ninguna de las top-impact.

**Su justificación real (por qué existió).** La Opción A fue la primera implementación, y
se eligió porque:

- **Era la más rápida de implementar**: un *join* por `query_id` en memoria, sin configurar
  ni parsear logs, reutilizando una sección de telemetría que el pipeline ya recolectaba.
- **Servía para las pruebas de diseño**: en un banco controlado donde se "cuelga" una query
  larga a propósito (un `SELECT` lento mantenido abierto), la query sí permanece activa al
  tomar el snapshot y la opción la encuentra; como demo de concepto validaba el flujo
  completo hasta `EXPLAIN`.

**Por qué no es sólida en producción.** Su premisa —"la query candidata todavía está
ejecutándose cuando el pipeline la mira"— depende del timing y de la duración de la query:

1. Las queries cortas y rápidas (la mayoría del tráfico transaccional real) **nunca** se
   capturan: entre dos snapshots ya completaron y salieron de `pg_stat_activity`.
2. La recuperación solo funciona para queries *long-running*, y aun así hay una ventana de
   tiempo en la que la query no está activa (antes de empezar, o tras terminar).
3. Es silenciosa: si la query no está en vuelo, simplemente no se marca; el informe queda
   incompleto sin señal de error.
4. No hay memoria histórica: una query que degradó y terminó hace segundos es irrecoverable
   por esta vía, justamente el caso de un incidente de rendimiento típico.

### Opción B — Logs del servidor (versión actual)

**Cómo funciona.** El `LogsBackfillStage` corre entre la etapa de candidatos y el `EXPLAIN`
y recupera la query real desde los logs de cada motor:

- PostgreSQL: `postgresql.csv` (csvlog con `duration`, `statement`/`execute`, y `detail`
  con los `parameters` de sentencias preparadas), parseado por columna.
- MySQL: `ql-slow.log` (slow log con bloques `# Query_time: ...` seguidos del SQL).

El flujo: se leen las últimas `N` líneas del log (cola, no todo el archivo;
`LOG_TAIL_LINES`, por defecto 200 000), se parsean las entradas, se calcula su **firma
canónica** (`fast_signature`: query en minúsculas, literales/números/placeholders
normalizados) y se construye un índice `firma → entrada`. Luego cada candidato se busca por
su firma: si hay coincidencia, se marca `real_query_found = True` y se reemplaza el
`query_text` por el **texto crudo del log** (con literales reales y, en PostgreSQL, con los
parámetros materializados si el `query_id` es de una sentencia preparada).

**Por qué dio 100 %.** El log es un registro **acumulado e histórico**: toda query que se
ejecutó y superó el umbral de duración del log está escrita, haya terminado hace un segundo
o hace un minuto. La recuperación es *a posteriori* y no depende de que la query siga en
curso cuando el pipeline la mira. Además el índice por firma canónica tolera diferencias de
formato entre motores (backticks de MySQL, `$1` de PostgreSQL preparadas) y el ruido
posterior al top-impact no afecta porque la firma identifica la sentencia, no su posición.

---

## 9.3 Pregunta 1 — ¿Cuál alternativa ofrece mejor desempeño bajo carga esperada?

| Criterio | Opción A (actividad) | Opción B (logs) |
|----------|----------------------|-----------------|
| **Latencia promedio y máxima** | Casi nula: es un lookup en memoria sobre datos ya recolectados; no agrega latencia perceptible al snapshot. | Baja y acotada: lee la cola del log por bloques (desde el final) con un límite fijo de líneas; la latencia de parseo depende del volumen de la ventana, no del tiempo total de vida del log. |
| **Throughput (capacidad de procesamiento)** | Constante pero *inútil*: cero latencia por snapshot, sin embargo recupera 0 % de las queries cortas que dominan la carga real. | Escala con la ventana de log; el índice por firma es una sola pasada y los candidatos son como máximo 10+ por snapshot, así que el coste de matching es despreciable frente al parseo. |
| **Comportamiento bajo carga concurrente** | Solo atrapa lo que esté *en vuelo* al instante del snapshot; con muchos usuarios simultáneos captura algunas queries largas, pero las cortas y ya terminadas se pierden por diseño. | Independiente de la concurrencia: cada sentencia ejecutada deja su huella en el log, así que la tasa de recuperación no depende de cuántos usuarios haya ni de cuánto duren sus queries. |

**Veredicto:** Opción B. Aunque la A tiene coste de cómputo mínimo, "desempeño" aquí no es
solo menor latencia: es **eficacia bajo la carga esperada** (miles de queries cortas
transaccionales). La A degrada la *calidad* del informe a la misma carga que la B maneja
al 100 %. La B mantiene el sobrecosto del pipeline dentro de la restricción del < 5 %
(verificada en `measure_overhead.py`), porque el trabajo de logs es acotado por ventana.

---

## 9.4 Pregunta 2 — ¿Qué grado de acoplamiento introduce cada opción?

| Criterio | Opción A (actividad) | Opción B (logs) |
|----------|----------------------|-----------------|
| **Dependencia de servicios externos** | Ninguna extra: usa las vistas de observabilidad del propio motor que el pipeline ya consulta (`pg_stat_activity`, `events_statements_current`). | Alta: requiere el log del servidor (configuración de `logging_collector`/`slow_query_log`, rutas y formato del csvlog/slow log por motor) y su disponibilidad continua. |
| **Interdependencia entre módulos internos** | Baja en superficie pero frágil: depende de la coincidencia de `query_id` entre `statements` y `active_queries` (un contrato entre dos secciones recolectadas). | Mediana y contenida: el acoplamiento vive en dos módulos aislables (`stages/log_reader.py`, `config/logs.py`); el stage `LogsBackfillStage` solo toca `top_impact_queries`, y si falla vuelve sin modificar el contrato del snapshot. |
| **Facilidad de sustitución de componentes** | Alta: es un dict lookup, reemplazable en minutos. | Media: sustituir la vía de logs exige reimplementar el parser por motor, pero está detrás del mismo entry-point (`build_log_index`), registrable como normalizador por dialecto (patrón Registry ya usado). |

**Veredicto:** la A introduce *menos* acoplamiento, pero ese bajo acoplamiento es
exactamente lo que la hace inútil en producción: se amarra a un instante de la actividad,
sin estado histórico. La B paga un acoplamiento mayor con infraestructura (logs), pero lo
contiene en módulos dedicados, manteniendo el resto del pipeline desacoplado del formato de
cada motor. La sustitución de la A por la B no requirió rediseñar el pipeline: fue agregar
un stage y quitar unos renglones del selector (`c730da1`).

---

## 9.5 Pregunta 3 — ¿Qué nivel de disponibilidad y tolerancia a fallos ofrece cada alternativa?

| Criterio | Opción A (actividad) | Opción B (logs) |
|----------|----------------------|-----------------|
| **Tiempo de disponibilidad (uptime esperado)** | Bajo: la "disponibilidad" de la recuperación equivale a la probabilidad de que una query candidata esté ejecutándose al instante del snapshot (≈ 0 % para queries cortas; aceptable solo para long-running). | Alto: el log acumula todo lo ejecutado, así que la ventana de recuperación cubre la historia reciente completa, no un instante. |
| **Mecanismos de recuperación ante fallos** | Ninguno: si no hay match, el candidato queda "no encontrado" sin reintento ni señal clara. | Varios: lectura por cola con límite (evita reprocesar todo el log viejo), filtro por ventana temporal (`since`, vía `QL_LOG_SINCE`), salto de filas parciales en la frontera, umbral mínimo de duración y, en PostgreSQL, validación de parámetros completos antes de materializar placeholders. |
| **Impacto de fallos parciales** | Degrada en silencio: si la tabla de actividad está vacía, sin permisos o el motor está saturado, la recuperación simplemente no ocurre y el informe sale incompleto sin aviso. | Controlado: si el log falta (`Path.exists()` falso), el stage loguea una advertencia y continúa (no aborta el pipeline); si una entrada no matchea, la candidata queda como no encontrada y el resto del snapshot sigue intacto. |

**Veredicto:** Opción B. La A no ofrece mecanismos de recuperación inherentes —su única
"garantía" es el azar del timing— y su fallo es silencioso. La B degrada de forma explícita
y acotada (warning + candidata sin marcar), no arrastra el resto del snapshot y cubre la
historia reciente completa, que es exactamente la disponibilidad que necesita un
diagnóstico de un incidente que ya terminó.

---

## 9.6 Opción seleccionada y justificación de diseño

Se selecciona la **Opción B (logs del servidor)** como la alternativa para producción:

- **Recuperación determinista e histórica**: 100 % de los candidatos en ambos motores bajo
  la carga de prueba, independiente del timing del snapshot.
- **Restricción de overhead respetada**: lectura acotada por cola y por ventana temporal; el
  parseo es de una sola pasada y el matching por firma es O(1) por candidato.
- **Degradación controlada**: ante fallos de log, el pipeline continúa y registra el motivo
  en vez de entregar un informe incompleto en silencio.
- **Costo de implementación razonable**: la diferencia frente a la A fue un stage dedicado
  (`LogsBackfillStage`) con parsers por motor detrás de un único entry-point
  (`build_log_index`), sin tocar el contrato del snapshot.

La **Opción A se conserva como referente documentado** (y como mecanismo de bajo costo para
pruebas de diseño rápidas, donde se pueden colgar queries largas controladas), pero queda
descartada para el banco reproducible de precisión/exhaustividad y para cualquier uso sobre
una base de datos en operación.

## 9.7 Evidencia y pruebas incluidas

| Prueba | Script | Salida | Qué responde |
|--------|--------|--------|--------------|
| Comparativa A vs B | `compare_real_query_mechanics.py` | `results/compare_real_query_mechanics.{json,txt}` | Tasa de recuperación de cada mecánica sobre los mismos snapshots |
| Ablación de ventana (segunda prueba) | `ablate_recovery_window.py` | `results/ablate_recovery_window.{json,txt}` | En qué casos la Opción B deja de recuperar (ventana de log × tráfico posterior) |

Reproducción:

```
venv\Scripts\python.exe compare_real_query_mechanics.py [MUESTRAS] [REPS] [NOISE]
venv\Scripts\python.exe ablate_recovery_window.py [MUESTRAS] [REPS]
```