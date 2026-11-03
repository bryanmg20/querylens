# Seguimiento de problemas: ventanas, marcas y reportes (propuesta, sin implementar)

> **Estado:** propuesta, nada implementado. Surgió el 2026-11-03 al revisar AP-05 (índice no utilizado o redundante).
> Amplía `001_ciclo_vida_hallazgos.md`: toma su clave por patrón (sección 3.1) y sus reglas de cierre (sección 3.4), pero las usa para **verificar las marcas del usuario** y para **comparar periodos**, en vez de cerrar hallazgos solas.

## 1. De dónde salió

### 1.1 La pregunta

> Si las `top_impact_queries` cambian, ¿cambia también si se detecta o la consulta que se asocia? Por ejemplo, un índice tiene 3 consultas posibles y se escoge una; si en el siguiente snapshot la escogida sale de `top_impact_queries`, ¿se asigna otra de las 2 restantes? Y si solo hay una, ¿se deja de reportar?

### 1.2 La respuesta

Cada snapshot se evalúa desde cero: el detector no recuerda qué consulta eligió antes. En cada snapshot arma de nuevo las candidatas con las consultas que están entre las de mayor impacto **de ese snapshot** y que corrieron **en esa ventana** de 10 s.

**Hay 3 consultas y la elegida sale.** En el siguiente snapshot se elige otra de las 2 que quedan, con el criterio de siempre: primero las que filtran por la primera columna del índice, después la que recorre la tabla completa por esa columna, y después la de más tiempo en la ventana. El hallazgo sale igual, con otro `query_id`.

```
snapshot 1: candidatas {Q1, Q2, Q3} -> elige Q1 -> hallazgo con query_id Q1
snapshot 2: candidatas {Q2, Q3}     -> elige Q2 -> hallazgo con query_id Q2
```

**Hay 1 sola y sale.** En ese snapshot no hay hallazgo, y vuelve a salir cuando la consulta vuelve. El índice sigue sin uso o redundante: lo que se pierde es solo el reporte de ese snapshot.

```
snapshot 1: {Q1} -> hallazgo
snapshot 2: {}   -> nada
snapshot 3: {Q1} -> hallazgo otra vez
```

Lo mismo vale para el subtipo redundante. En los patrones que se evalúan por consulta, de AP-01 (degradación respecto a la línea base) a AP-08 (error grave de estimación de cardinalidad) salvo AP-05, la consulta **es** el problema: si no corrió en la ventana, no hay nada que evaluar.

### 1.3 Cuándo pasa en la práctica

Una consulta deja de ser candidata por dos motivos:

1. **Sale de `top_impact_queries`.** Pasa poco. La lista son las 10 de más tiempo **acumulado desde que arrancaron los contadores** (`ORDER BY total_exec_time DESC` en Postgres, `SUM_TIMER_WAIT DESC` en MySQL; `selectors.select_high_impact_time_statements`), más las inestables y las que escriben a disco. Como es acumulado, cambia despacio.
2. **No corrió en esa ventana de 10 s.** Es el caso común. Una consulta que corre una vez por minuto es candidata en 1 de cada 6 snapshots. Además, puede seguir entre las de mayor impacto por su tiempo acumulado aunque ya no corra: el detector la descarta porque su `execution_count` no subió.

## 2. Problemas actuales que esto deja ver

### 2.1 Una fila por snapshot

`storage.save_findings` inserta una fila nueva en `public.hallazgos` cada vez que una regla dispara. Con ventanas de 10 s, un problema que sigue igual genera unas 6 filas por minuto: en el escenario de AP-05, 4 filas por índice en 4 ciclos.

### 2.2 El mismo problema con `query_id` distintos y huecos

Con el índice `ql_scn_pad` sin uso:

| detectado_en | índice | subtipo | query_id |
|---|---|---|---|
| 10:00:00 | ql_scn_pad | no usado | Q1 |
| 10:00:10 | ql_scn_pad | no usado | Q1 |
| 10:00:20 | ql_scn_pad | no usado | **Q2** (Q1 no corrió en esa ventana) |
| 10:00:30 | ql_scn_pad | no usado | Q1 |
| *(10:00:40: sin fila; no corrió ninguna consulta que filtre la tabla)* | | | |
| 10:00:50 | ql_scn_pad | no usado | Q1 |

- **`query_id` distintos:** es el mismo problema, el mismo índice sin uso, y aparece asociado a veces a Q1 y a veces a Q2.
- **Huecos:** parece que el problema se resolvió a las 10:00:40 y volvió a aparecer, cuando el índice nunca dejó de estar sin uso.

### 2.3 El reporte del CLI agrupa mal (error actual)

`cli/share/report.sql` junta las filas repetidas con `DISTINCT ON (antipatron, query_id, database_name, table_name)`. Esa clave no identifica el problema en todos los patrones:

- **AP-05 (índice no utilizado o redundante) junta índices distintos.** Si `ql_scn_pad` y `ql_scn_c_k`, los dos sin uso en la misma tabla, se asocian a la misma Q1, caen en un solo grupo y el reporte muestra **solo uno**.
- **AP-05 separa el mismo índice.** Si `ql_scn_pad` se asoció a Q1 y a Q2, sale en dos líneas.
- **AP-03 (predicado no sargable) junta columnas distintas.** Una consulta con dos columnas no sargables en la misma tabla da dos hallazgos que caen en el mismo grupo.
- **Por revisar:** AP-04 (índice ausente) con dos escaneos de la misma tabla en una consulta, y los patrones de contención.

### 2.4 La interfaz muestra filas sueltas

`api_rest/app/routers/diagnostics.py` devuelve las últimas `MAX_FINDINGS` filas de `hallazgos` por fecha, sin agrupar, y `frontend/components/FindingsPanel.tsx` las lista una debajo de otra. El mismo problema aparece cientos de veces.

## 3. Las dos formas de uso

### 3.1 Por ventanas (como lo usaría el profesor)

> Dejarlo corriendo X horas, por ejemplo una jornada laboral; ver los hallazgos; corregir; y al día siguiente dejarlo otras X horas para ver si los problemas se solucionaron.

```
schedule (jornada 1) -> reporte -> corrige -> schedule (jornada 2) -> reporte que compara con la jornada 1
```

Pregunta que responde: **¿qué cambió entre la jornada anterior y esta?**

Buena parte ya existe en el CLI:

- `querylens schedule ID --from T --until T` guarda `collect_from` y `collect_until` en `registered_databases`.
- El servicio `scheduler` enciende `monitoring_enabled` a la hora de inicio, lo apaga a la de fin y, `REPORT_GRACE_S` después, escribe el reporte en `reports/` y anota `report_written_at`.
- `querylens report ID [--since 24h | --from T --until T]` escribe un reporte de cualquier rango.

**Falta:** agrupar bien (2.3) y comparar con la ventana anterior.

### 3.2 Continuo, con marcas

> Sin establecer ningún periodo: encenderlo, pararlo cuando se quiera, seleccionar lo que se corrigió y esperar a que no se vuelva a reportar.

```
lo enciende -> ve los problemas abiertos -> marca "corregí este" -> sigue corriendo -> el sistema dice si volvió o si se confirmó
```

Pregunta que responde: **¿el problema que marqué volvió a aparecer?**

**Falta:** todo. Un estado por problema, la acción de marcar y la verificación posterior.

### 3.3 Cómo conviven

No son dos sistemas, son **dos formas de mirar los mismos datos**. El detector trabaja siempre igual mientras el monitoreo esté encendido, sin importar cómo lo encendió quien lo usa:

1. Encuentra los hallazgos; cada uno trae su clave (`finding_key`).
2. Guarda la detección en `hallazgos`, el registro con hora.
3. Actualiza el problema en `problemas`: última vez visto, cantidad de veces y, si estaba marcado, su verificación.

Lo que cambia es quién enciende el monitoreo y qué se lee:

| Dato | Por ventanas | Continuo, con marcas |
|---|---|---|
| `hallazgos`, el registro con hora | sí: el reporte filtra por fechas y compara | sí: el historial de cada problema |
| `problemas`, el estado | opcional: el reporte muestra las marcas si las hay | sí: marcas y estados reapareció o confirmado |

Se pueden mezclar: el profesor puede marcar en medio de sus ventanas, y quien usa el modo continuo puede pedir un reporte de cualquier rango. **Programar una ventana** solo decide **cuándo** se enciende el monitoreo; **marcar** solo agrega un estado a un problema. Son independientes.

Ejemplo con los mismos datos:

```
Lunes  08:00 ---------- 18:00   monitoreo encendido -> ql_scn_pad detectado 3.400 veces
Lunes  19:00                    DROP INDEX ql_scn_pad
Martes 08:00 ---------- 18:00   monitoreo encendido -> no se detecta
```

- **Por ventanas:** el reporte del martes dice «`ql_scn_pad` no usado: lunes 3.400 veces, martes 0 → ya no aparece». Nadie marcó nada.
- **Con marcas:** el lunes a las 19:00 alguien lo marca como corregido; el martes el problema pasa a **confirmado**, porque el índice ya no existe.

## 4. Diseño propuesto

### 4.1 Clave por problema (`finding_key`)

Cada detector arma la clave que identifica **el problema**, no la detección. Es la tabla 3.1 del doc 001:

| Patrón | Clave | Ejemplo |
|---|---|---|
| AP-01 degradación respecto a la línea base | `query_id` | `Q1` |
| AP-02 escaneo completo evitable | `query_id` + tabla | `Q1:ql_demo.ql_scn` |
| AP-03 predicado no sargable | `query_id` + tabla + columna | `Q1:ql_demo.ql_scn:k` |
| AP-04 índice ausente | `query_id` + tabla + columnas | `Q1:ql_demo.ql_scn:c` |
| AP-05 índice no utilizado o redundante | índice + subtipo (**sin consulta**) | `ql_demo.ql_scn.ql_scn_pad:no_usado` |
| AP-06 patrón N+1 | `query_id` de la hija + constraint | `Q9:fk_orders_customer` |
| AP-07 vertido a disco | `query_id` | `Q1` |
| AP-08 error grave de estimación de cardinalidad | `query_id` | `Q1` |

- Va en `Hallazgo.finding_key` (`detection_engine/models.py`) y se guarda en una columna nueva de `hallazgos`.
- La clave siempre se usa junto con `db_id` y `antipatron`.
- Incluir el schema evita mezclar tablas con el mismo nombre en schemas distintos.
- **Patrones de contención (CT-01 a CT-05):** también escriben en `hallazgos` (`detection_engine/deadlocks/facade.py`). Ese módulo es del compañero: hay que acordar sus claves o dejarlos fuera de la primera versión.

### 4.2 Tabla `hallazgos` (ya existe): el registro de detecciones

Sigue siendo una fila por detección, como hoy. Solo se agrega:

```sql
ALTER TABLE public.hallazgos ADD COLUMN IF NOT EXISTS finding_key TEXT;
CREATE INDEX IF NOT EXISTS hallazgos_key_idx ON public.hallazgos (db_id, antipatron, finding_key, detectado_en);
```

Es lo que leen el reporte y la comparación entre periodos.

### 4.3 Tabla `problemas` (nueva): una fila por problema

| Columna | Tipo | Para qué |
|---|---|---|
| `db_id`, `antipatron`, `finding_key` | TEXT | Identidad; clave primaria o índice único |
| `first_seen`, `last_seen` | TIMESTAMPTZ | Primera y última detección |
| `veces` | INT | Cuántas veces se detectó |
| `severidad` | TEXT | Ver la decisión D6 |
| `ultimo_hallazgo_id` | BIGINT | La última fila de `hallazgos`, con su evidencia, explicación y `query_id` |
| `estado` | TEXT | `abierto` / `marcado_corregido` / `confirmado` / `reapareció` |
| `marcado_en`, `marcado_por` | TIMESTAMPTZ, TEXT | Cuándo y quién lo marcó |
| `verificaciones` | INT | Ventanas sin detección con evidencia de que se puede verificar (sección 4.4) |
| `referencia` | JSONB | Valor guardado al abrir o al marcar, por ejemplo la mediana de AP-01 |

Quién escribe:

- **El detector**, después de los detectores y en la misma transacción que `save_findings`: crea o actualiza la fila de cada clave detectada y verifica las marcadas.
- **El usuario**, desde la interfaz (a través de `auth_service` o `api_rest`) o el CLI: pasa un problema a `marcado_corregido`.

Estados:

```
            detectado por primera vez
                     |
                     v
   +----------> [abierto] --- el usuario lo marca ---> [marcado_corregido]
   |                                                     |            |
   |                                    se vuelve a detectar   K verificaciones sin detección
   |                                                     |            |
   |                                                     v            v
   +--- el usuario lo vuelve a abrir o lo marca otra vez --- [reapareció]  [confirmado]
                                                                         |
                                                       se vuelve a detectar -> [reapareció]
```

### 4.4 Verificar una marca

En cada snapshot, para cada problema en `marcado_corregido` o `confirmado`:

| Qué pasa | Resultado |
|---|---|
| El detector lo vuelve a encontrar | **reapareció**: la corrección no funcionó o se deshizo |
| No lo encuentra **y** hay evidencia de que se puede verificar | `verificaciones` += 1; al llegar a K, **confirmado** |
| No lo encuentra y no hay con qué verificar | queda en `marcado_corregido` («esperando verificación») |

**Qué cuenta como evidencia**, por patrón (sale de las reglas de cierre del doc 001):

| Patrón | Evidencia de que se puede verificar |
|---|---|
| AP-05 índice no utilizado o redundante | El índice tuvo lecturas o ya no existe en `snapshot.indexes`. Confirma de una vez, sin K |
| AP-01 degradación respecto a la línea base | La consulta corrió y su tiempo medio está cerca de la `referencia` guardada (por ejemplo ≤ 1,5×). **No** basta con «no se detectó»: la regla de AP-01 deja de disparar sola a las 5 ventanas (doc 001, sección 1.1) |
| AP-02, AP-03, AP-04, AP-06, AP-07, AP-08 | La consulta (en AP-06, la hija) corrió en la ventana, con delta de `execution_count` mayor que 0, y la regla no disparó |

- **Dónde se calcula:** en un paso nuevo después de los detectores, en `main.py`. Tiene el snapshot, `history` y las claves detectadas.
- **Qué no se rompe:** los detectores siguen independientes y sin estado.
- **Un detalle de tiempo:** las detecciones de snapshots tomados antes de la marca, procesados después por la cola, no deben contar como reaparición. Hay que comparar contra el `captured_at` del snapshot, no contra la hora de inserción.

### 4.5 Historial de periodos (tabla nueva, opcional)

`registered_databases` guarda solo **la ventana actual**: al programar otra, se pisa la anterior. Para listar las jornadas pasadas hace falta un historial:

| Columna | Para qué |
|---|---|
| `db_id` | La base |
| `desde`, `hasta` | El periodo |
| `origen` | `programado` (scheduler) o `manual` (interruptor) |
| `reporte` | Ruta del reporte, si se escribió |

Quién escribe:

- **El scheduler**, al encender y apagar una ventana programada (`cli/querylens.sh`, el ciclo que revisa `collect_from` y `collect_until`).
- **`auth_service`**, al encender y apagar el interruptor de la interfaz (`set_monitoring` en `routers/databases.py`). Hoy solo cambia `monitoring_enabled` y no guarda cuándo.
- **El CLI**, con `pause` y `resume`, si se usan.

Sirve para elegir los periodos en «Comparar periodos» y para ofrecer «desde que lo encendí» en el reporte manual. La alternativa sin esta tabla es escribir las fechas a mano (decisión D4).

### 4.6 Reporte

Funciona igual en las dos formas de uso, porque lee `hallazgos` en un rango:

| | Por ventanas | Continuo |
|---|---|---|
| Cuándo se genera | Solo, al terminar la ventana (scheduler) | Cuando se pide |
| Rango | La ventana programada | El que se elija: «desde que lo encendí», «últimas 24 h», dos fechas |
| CLI | Automático (ya existe) | `querylens report ID --since 24h` o `--from/--until` (ya existe) |
| Interfaz | Enlace «reporte listo» en la lista de periodos | Botón «Descargar reporte» con el rango |

Cambios en `cli/share/report.sql`:

1. **Agrupar por `finding_key`** en vez de `(antipatron, query_id, database_name, table_name)`. Corrige el error de la sección 2.3.
2. **Comparar con el periodo anterior** de la misma base, si existe:

   | Estado | Significa |
   |---|---|
   | nuevo | no estaba en el periodo anterior |
   | persiste | estaba y sigue |
   | ya no aparece | estaba y no se detectó en este, con evidencia de que se pudo verificar |
   | sin datos | estaba, no se detectó, y no hay con qué verificar (por ejemplo, la consulta no corrió) |

3. **Mostrar el estado del problema**, si hay marca: «marcado como corregido lun 19:00 → confirmado».

Para distinguir **ya no aparece** de **sin datos** hay que saber si la consulta corrió en el periodo. `statement_samples` no sirve: guarda **solo la última fila** por consulta (es un upsert). Las opciones están en la decisión D5.

### 4.7 Interfaz

En la página de cada base:

```
+- Base: ql_demo (Postgres) ---------------------------------------------+
| Monitoreo                                                               |
|   Modo:  (o) Manual      ( ) Programado                                 |
|   Manual:      [ * Encendido ]        <- el interruptor de hoy          |
|   Programado:  Desde [2026-11-04 08:00]  Hasta [2026-11-04 18:00]       |
|                [Programar]                                              |
|   Periodos anteriores: lun 08-18 (reporte listo) - mar 08-18 ...        |
|   [Descargar reporte: desde ___ hasta ___]                              |
+-------------------------------------------------------------------------+
| [ Problemas ]  [ Comparar periodos ]                                    |
+-------------------------------------------------------------------------+
```

**Pestaña «Problemas»** (forma continua; lee `problemas`):

```
Filtro: (o) Abiertos  ( ) Marcados  ( ) Confirmados  ( ) Reaparecidos

ALTA  AP-04 índice ausente  orders.customer_id
      visto 1.200 veces - último hace 10 s          [Marcar corregido]
BAJA  AP-05 índice no usado  ql_scn_pad
      visto 3.400 veces - último hace 10 s          [Marcar corregido]
!  REAPARECIÓ  AP-03 predicado no sargable  users.email
      marcado corregido ayer 19:00 - volvió hoy 09:12
```

**Pestaña «Comparar periodos»** (forma por ventanas; lee `hallazgos`):

```
Periodo A: [lun 08:00-18:00 v]   Periodo B: [mar 08:00-18:00 v]
           (lista del historial de periodos, o fechas a mano)

Problema                              |   A   |   B   | Estado
AP-05 índice no usado ql_scn_pad      | 3.400 |   0   | ya no aparece
AP-04 índice ausente orders.cust_id   | 1.200 | 1.150 | persiste
AP-02 escaneo completo items (Q7)     |   0   |   80  | nuevo
```

«Fechas a mano» quiere decir que, sin historial de periodos, cada periodo se elige con dos calendarios (desde y hasta) en vez de una lista. Funciona igual, porque cada fila de `hallazgos` tiene su hora; solo es menos cómodo.

### 4.8 CLI

| Comando | Estado |
|---|---|
| `querylens schedule ID --from T --until T` | ya existe |
| `querylens report ID [...]` | ya existe; pasa a agrupar por `finding_key` y a comparar con el periodo anterior |
| `querylens findings ID [--estado abierto\|marcado\|confirmado\|reaparecio]` | nuevo: lista `problemas` |
| `querylens resolve ID <problema>` | nuevo: marca como corregido |
| `querylens reopen ID <problema>` | nuevo, opcional: deshace la marca |

## 5. Dudas que surgieron y respuestas

| Duda | Respuesta |
|---|---|
| Si `top_impact_queries` cambia, ¿cambia la consulta asociada? ¿Y si solo hay una, se deja de reportar? | Sí a las dos. Ver la sección 1.2 |
| ¿Qué significa «filas con `query_id` distintos y huecos»? | Que el mismo problema aparece en varias filas con consultas distintas y con snapshots sin fila. Ver la sección 2.2 |
| ¿Hace falta una tabla auxiliar? ¿Es solo para AP-05 o para todos? | Para todos: los ocho patrones se repiten igual y solo cambia la clave. La propuesta del doc 001 eran columnas en `hallazgos`; con las dos formas de uso conviene una tabla `problemas` aparte (decisión D1) |
| ¿Cómo encaja el uso del profesor (jornadas, corregir, otra jornada)? | Es la forma por ventanas: `schedule` y `report` ya existen; falta agrupar bien y comparar con el periodo anterior (sección 3.1) |
| ¿Funcionaría como el CLI? | Sí: los mismos `schedule` y `report`; el reporte del día 2 trae la columna de estado (sección 4.6) |
| ¿No sería mejor tener las dos formas? | Sí, y comparten la base: la clave y el registro (sección 3.3) |
| ¿Cómo conviven las dos formas? ¿Cuáles son las dos tablas? | `hallazgos`, el registro de detecciones, y `problemas`, el estado por problema. Son dos pestañas en la interfaz que leen los mismos datos (secciones 3.3, 4.2, 4.3 y 4.7) |
| ¿Dónde se programa la ventana? | Hoy solo en el CLI. En la interfaz iría junto al interruptor actual, con un endpoint nuevo en `auth_service` (sección 4.7) |
| ¿Qué es «fechas a mano»? | Elegir cada periodo con un calendario de desde y hasta, en vez de una lista de periodos pasados (sección 4.7) |
| ¿Se puede sacar reporte de las dos formas? | Sí: automático al terminar una ventana, o a pedido de cualquier rango (sección 4.6) |

## 6. Decisiones abiertas y alternativas

### D1. Dónde guardar el estado de cada problema

| Opción | Pros | Contras |
|---|---|---|
| **A. Tabla `problemas` aparte; `hallazgos` sigue como registro** *(recomendada)* | El reporte y la comparación siguen contando detecciones por periodo; las marcas viven en un solo lugar; `hallazgos` no cambia de significado | Una tabla más y la lógica de actualización |
| B. Columnas en `hallazgos` y una fila que se actualiza (doc 001, sección 3.2) | Una sola tabla | Se pierde el conteo por periodo (una fila abierta cubre lunes y martes), así que la comparación entre jornadas ya no se puede hacer con `hallazgos` |
| C. Solo `finding_key` en `hallazgos`, sin estados | Lo mínimo; alcanza para la forma por ventanas | No permite marcar |

### D2. Cerrar automáticamente o solo con la marca del usuario

| Opción | Pros | Contras |
|---|---|---|
| **A. Solo el usuario marca; el sistema confirma o avisa que reapareció** *(recomendada)* | El usuario decide qué corrigió; no se cierran problemas por falta de datos | Lo que nadie marca queda abierto para siempre (se puede filtrar por `last_seen`) |
| B. Cierre automático con las reglas del doc 001 (recuperado, nueva normalidad, sin actividad) | No hace falta marcar nada | Un problema se puede cerrar porque la consulta dejó de correr; AP-01 necesita la referencia anclada |
| C. Las dos: marcas del usuario, más cierre automático por inactividad (por ejemplo, 24 h sin detectar) | Limpia lo viejo sin intervención | Más estados que explicar |

### D3. Qué pasa cuando un problema confirmado vuelve a aparecer

| Opción | Pros | Contras |
|---|---|---|
| **A. La misma fila pasa a `reapareció`** *(recomendada)* | Se ve la historia: «se corrigió y volvió» | Hay que guardar cuántas veces reapareció |
| B. Se abre una fila nueva | Más simple | Se pierde la relación con la marca anterior |

### D4. Cómo elegir los periodos a comparar

| Opción | Pros | Contras |
|---|---|---|
| **A. Tabla de historial de periodos (sección 4.5)** *(recomendada)* | Lista de jornadas para elegir; «desde que lo encendí» en el modo manual | Una tabla más; el scheduler y `auth_service` tienen que escribirla |
| B. Fechas a mano | Sin tablas nuevas | Hay que acordarse de cuándo fue cada jornada |
| C. Deducir los periodos de los huecos en `hallazgos` | Sin tablas nuevas | Poco confiable: un periodo sin hallazgos no deja rastro |

### D5. Cómo distinguir «ya no aparece» de «sin datos»

| Opción | Pros | Contras |
|---|---|---|
| **A. Contar, en `problemas`, las ventanas verificadas por periodo (sección 4.4)** *(recomendada)* | Se reutiliza la verificación de las marcas | Solo existe para problemas que ya se conocían; alcanza, porque solo se compara lo que estaba en el periodo anterior |
| B. Guardar la actividad de cada consulta por ventana (historial de `statement_samples`) | Dato completo | Mucho volumen: una fila por consulta cada 10 s |
| C. No distinguir; mostrar «ya no aparece (verificar que la consulta haya corrido)» | Nada nuevo | El usuario puede creer que algo se resolvió cuando solo no corrió |

### D6. Severidad del problema

| Opción | Pros | Contras |
|---|---|---|
| **A. La máxima alcanzada** *(recomendada)* | No esconde un pico | Un problema que mejoró sigue con la severidad alta |
| B. La de la última detección | Refleja el estado actual | Un problema intermitente cambia de severidad a cada rato |

### D7. Alcance de la primera versión

| Opción | Pros | Contras |
|---|---|---|
| **A. AP-01 a AP-08; CT-01 a CT-05 después, con el compañero** *(recomendada)* | No toca el módulo de otro | Los abrazos mortales siguen como filas sueltas |
| B. Todos los patrones | Uniforme | Hay que coordinar las claves de contención |
| C. Empezar solo con AP-05 | Más chico | Igual hay que tocar el reporte, la interfaz y el almacenamiento |

### D8. Dónde vive la programación de ventanas en la interfaz

| Opción | Pros | Contras |
|---|---|---|
| **A. Endpoint en `auth_service`, junto a `set_monitoring`** *(recomendada)* | Es el servicio que ya escribe `registered_databases` desde la interfaz | — |
| B. Endpoint en `api_rest` | — | `api_rest` hoy solo lee; habría que darle permiso de escritura sobre `registered_databases` |

### D9. Hallazgos que ya existen

| Opción | Pros | Contras |
|---|---|---|
| **A. Dejarlos con `finding_key` vacío, como historial** *(recomendada)* | Sin migración | Los reportes de fechas viejas siguen agrupando como antes |
| B. Calcular la clave desde la evidencia | Reportes viejos corregidos | No todas las evidencias tienen los datos; en AP-05 sí (`index_name`, `subtipo`) |

### D10. Volumen de `hallazgos`

Con ventanas de 10 s, un problema que sigue genera unas 3.400 filas por jornada de 10 h.

| Opción | Pros | Contras |
|---|---|---|
| **A. Dejarlo como está por ahora** *(recomendada)* | El conteo por periodo es exacto | Crece rápido; hace falta el índice de la sección 4.2 |
| B. Insertar en `hallazgos` solo cuando cambia algo (severidad, consulta asociada, evidencia), o como mucho cada N minutos | Mucho menos volumen | «Veces» se calcula distinto; la comparación entre periodos tiene que leer de `problemas` |
| C. Borrar o resumir detecciones viejas (por ejemplo, más de 30 días) | Acota el tamaño | Se pierde detalle antiguo |

### D11. Quién marca y cómo se registra

| Opción | Pros | Contras |
|---|---|---|
| **A. `marcado_por` = el usuario del token en la interfaz, el usuario del sistema en el CLI** *(recomendada)* | Queda trazabilidad | — |
| B. Sin registrar quién | Más simple | No se sabe quién dijo que estaba corregido |

### D12. Parámetros

Hay que declarar en el informe que son parte del **seguimiento**, no de la detección:

- **K** verificaciones para confirmar: por ejemplo 3 ventanas, es decir 30 s con ventanas de 10 s. Quizá convenga contarlo en tiempo y no en ventanas.
- **Margen de AP-01** para confirmar la recuperación: por ejemplo ≤ 1,5× la referencia.
- **Inactividad**, si se elige la opción D2-C: por ejemplo 24 h, igual que `STALE_AFTER`.

## 7. Archivos a tocar

| Archivo | Cambio | Fase |
|---|---|---|
| `detection_engine/models.py` | `Hallazgo.finding_key` | 1 |
| `detection_engine/anti_patterns/*.py` | Cada detector arma su clave | 1 |
| `querylens_database/hallazgos.sql` | Columna `finding_key` e índice | 1 |
| `detection_engine/storage.py` | Guardar `finding_key` | 1 |
| `cli/share/report.sql` | Agrupar por `finding_key` | 1 |
| `cli/share/report.sql`, `cli/querylens.sh` | Comparar con el periodo anterior | 2 |
| `querylens_database/problemas.sql` (nuevo), `service_roles.sql` | Tabla `problemas` y permisos | 3 |
| `detection_engine/storage.py`, `main.py` | Crear y actualizar `problemas`; verificar marcas (sección 4.4) | 3 |
| `cli/querylens.sh` | `findings`, `resolve`, `reopen` | 3 |
| `api_rest/app/routers/diagnostics.py`, `schemas.py` | Devolver problemas en vez de filas; comparación de periodos | 4 |
| `auth_service/app/routers/databases.py` | Marcar y desmarcar; programar y quitar ventanas | 4 |
| `frontend/components/FindingsPanel.tsx`, `app/diagnostics/page.tsx`, `types/database.ts` | Pestañas, botón de marcar, formulario de ventana | 4 |
| `querylens_database/periodos.sql` (nuevo); scheduler en `cli/querylens.sh`; `set_monitoring` | Historial de periodos (decisión D4-A) | 5 |
| `ql_sandbox/scenarios/run_scenario.py` | Escenarios que verifiquen la clave, la marca y la reaparición | 1-3 |
| `INSTALL_HEADLESS.md` | Comandos nuevos | 3 |

## 8. Fases

1. **Clave por problema:** `finding_key` en todos los detectores de AP-01 a AP-08, columna en `hallazgos` y reporte agrupado por clave. Corrige el error del reporte con AP-05 y AP-03, y es la base de todo lo demás.
2. **Comparación entre periodos en el reporte del CLI:** completa la forma por ventanas.
3. **Tabla `problemas`, marcas y verificación:** la forma continua desde el CLI (`findings`, `resolve`).
4. **API e interfaz:** pestañas «Problemas» y «Comparar periodos», botón de marcar y programación de ventanas.
5. **Historial de periodos:** lista de jornadas pasadas y «desde que lo encendí».

Cada fase se puede usar sin las siguientes.

## 9. Resumen y recomendación

- **El problema:** cada snapshot inserta filas sueltas en `hallazgos`. El mismo problema aparece miles de veces, con consultas asociadas distintas y con huecos, y el reporte del CLI agrupa con una clave que en AP-05 (índice no utilizado o redundante) y AP-03 (predicado no sargable) junta o separa problemas por error.
- **Las dos formas de uso** (por ventanas y continua con marcas) necesitan lo mismo: saber que la detección de hoy es el mismo problema que la de ayer.

**Recomendación:**

1. **Una clave por problema (`finding_key`) en cada detector**, con las claves de la sección 4.1. En AP-05 es índice + subtipo, sin la consulta, porque la consulta asociada puede cambiar en cada snapshot.
2. **Dos tablas con funciones distintas:**
   - `hallazgos`, el registro de detecciones de siempre más la clave;
   - `problemas`, una fila por problema con su estado (D1-A).
3. **Que el usuario marque y el sistema verifique**, sin cierre automático (D2-A). Si se vuelve a detectar, pasa a `reapareció` en la misma fila (D3-A). Se confirma con evidencia por patrón (sección 4.4): en AP-05, lecturas o índice eliminado; en AP-01, tiempo cerca de la referencia; en el resto, que la consulta haya corrido sin que la regla dispare.
4. **Un reporte que agrupe por clave y compare con el periodo anterior**, distinguiendo «ya no aparece» de «sin datos» (D5-A).
5. **Un historial de periodos** (D4-A), para elegir jornadas en la interfaz y ofrecer «desde que lo encendí».
6. **Hacerlo por fases.** La 1 sola ya corrige el reporte actual.

**Alternativas más baratas, si no hay tiempo para todo:**

- **Solo la fase 1** (D1-C): el reporte deja de mezclar problemas, pero no hay marcas ni comparación.
- **Fases 1 y 2 sin historial** (D4-B): la forma del profesor funciona completa desde el CLI, eligiendo fechas a mano.
- **Fases 1 a 3 sin interfaz:** las dos formas funcionan completas desde el CLI.
