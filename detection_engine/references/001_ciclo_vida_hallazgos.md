# Ciclo de vida de los hallazgos (propuesta, sin implementar)

> **Estado:** propuesta para después del MVP. Nada de esto está implementado.
> Surgió el 2026-10-12 al revisar AP-01 (degradación respecto a la línea base).

## 1. El problema

Hoy cada detector responde una sola pregunta por snapshot: *"¿en esta ventana se cumple la regla?"*. Cada vez que la respuesta es sí, `storage.save_findings` inserta **una fila nueva** en `public.hallazgos`. No existe la idea de un hallazgo "abierto" o "cerrado": son filas sueltas con su `detectado_en`, y la API muestra las últimas por fecha.

Eso trae dos consecuencias.

### 1.1 AP-01: una degradación permanente "desaparece" a los 5 minutos

La línea base de AP-01 es la mediana de las 10 ventanas anteriores, y las ventanas lentas también entran en ella. Con una consulta que pasa de 5 ms a 50 ms y se queda así:

| Ventana | Las 10 anteriores | Mediana | 50 / mediana | ¿La regla dispara? |
|---|---|---|---|---|
| 1 | 10 normales | 5 | 10× | sí |
| 2–5 | 6–9 normales + 1–4 lentas | 5 | 10× | sí |
| 6 | 5 normales + 5 lentas | (5 + 50) / 2 = 27,5 | 1,8× | **no** |
| 7+ | mayoría lentas | 50 | 1× | no |

En la ventana 6 el ratio es `2·lenta / (normal + lenta)`, que nunca llega a 2. Por eso **siempre** deja de disparar en la sexta ventana, sin importar cuánto se degradó. En la interfaz parece que el problema se resolvió solo, aunque la consulta siga lenta.

### 1.2 Los demás patrones: una fila por minuto

Una consulta que escribe a disco en cada ventana genera, con AP-07 (vertido a disco), 60 filas por hora con el mismo contenido. Lo mismo pasa con AP-06 (patrón N+1) mientras la aplicación siga igual, y con los patrones basados en EXPLAIN. Lo que interesa ver es **un** problema, abierto desde las 10:00, no 60 avisos.

## 2. La idea

Separar dos preguntas que hoy están mezcladas:

1. **¿Empezó un problema?** La responde la **regla del detector**, igual que hoy y sin cambios (Tabla 2 del informe).
2. **¿El problema sigue?** La responde una parte nueva, el **seguimiento del hallazgo**.

Funciona como un ticket de soporte. La regla es la alarma que lo abre, y el ticket queda abierto hasta comprobar que el problema se fue, aunque la alarma deje de sonar.

Con la consulta del ejemplo:

```
ventana 1   la regla dispara    -> se ABRE: "AP-01 query X, abierto desde v1, lo normal era 5 ms"
ventana 2-5 la regla dispara    -> el mismo hallazgo se actualiza (last_seen = v5)
ventana 6+  la regla ya no dispara, el seguimiento compara contra los 5 ms guardados
            -> sigue en 50 ms -> SIGUE ABIERTO
ventana 40  vuelve a 6 ms       -> se CIERRA: "del v1 al v40, recuperado"
```

La clave es que **al abrir se guarda el valor de referencia de ese momento** (los 5 ms). Aunque después la línea base de la regla se contamine, el seguimiento compara contra lo guardado.

### 2.1 Cambios legítimos

Si la consulta se volvió lenta con razón (la tabla creció 10×), el hallazgo no puede quedar abierto para siempre. Se cierra por cualquiera de estos motivos:

- **recuperado:** la consulta volvió cerca de su referencia;
- **nueva normalidad:** lleva demasiado tiempo abierto (por ejemplo, 60 ventanas = 1 hora) y se acepta el nuevo valor;
- **sin actividad:** la consulta dejó de ejecutarse (por ejemplo, 24 h, igual que `STALE_AFTER` en `statement_history.py`).

### 2.2 Precedente en el proyecto

`pipeline_health.issues` ya funciona así para los problemas de salud del pipeline: `first_seen`, `last_seen` y `resolved_at` (vacío = sigue abierto). Esta propuesta aplica la misma idea a `public.hallazgos`.

## 3. Diseño propuesto

### 3.1 Identidad de un hallazgo

Para saber si un aviso nuevo es "el mismo problema" que uno abierto hace falta una clave estable por patrón. Cada detector la arma; el resto del sistema solo la compara.

| Patrón | Clave propuesta |
|---|---|
| AP-01 degradación | `query_id` |
| AP-02 escaneo completo evitable | `query_id` + tabla |
| AP-03 predicado no sargable | `query_id` + tabla + columna |
| AP-04 índice ausente | `query_id` + tabla + columnas |
| AP-05 índice no utilizado o redundante | índice + subtipo |
| AP-06 patrón N+1 | `query_id` de la hija + constraint |
| AP-07 vertido a disco | `query_id` |
| AP-08 error de estimación de cardinalidad | `query_id` |

La clave siempre va junto con `db_id` y `antipatron`.

### 3.2 Columnas nuevas en `public.hallazgos`

| Columna | Tipo | Para qué |
|---|---|---|
| `finding_key` | TEXT | Identidad del problema (3.1) |
| `estado` | TEXT | `abierto` / `cerrado` |
| `first_seen` | TIMESTAMPTZ | Primera ventana en que la regla disparó (reemplaza a `detectado_en` como "inicio") |
| `last_seen` | TIMESTAMPTZ | Última ventana en que la regla disparó |
| `resolved_at` | TIMESTAMPTZ | Cuándo se cerró (NULL = abierto) |
| `motivo_cierre` | TEXT | `recuperado` / `nueva_normalidad` / `sin_actividad` |
| `referencia` | JSONB | Valores guardados al abrir, por ejemplo `{"baseline_median_ms": 5.0}` en AP-01 |
| `veces_detectado` | INT | Cuántas ventanas disparó la regla |

`evidencia`, `severidad`, `explicacion` y `recomendacion` se actualizan con la última detección; la severidad puede quedarse con la máxima alcanzada. Con un índice único parcial sobre `(db_id, antipatron, finding_key) WHERE estado = 'abierto'` se garantiza un solo abierto por problema.

### 3.3 Flujo por snapshot (en `main.py` / `storage.py`)

1. Los detectores corren igual que hoy y devuelven sus hallazgos (cada uno con su `finding_key`).
2. Para cada hallazgo:
   - si hay uno **abierto** con la misma clave, se actualizan `last_seen`, `veces_detectado`, la evidencia y la severidad;
   - si no, se inserta uno nuevo `abierto` con `first_seen = last_seen = captured_at` y su `referencia`.
3. Para cada hallazgo **abierto que no se volvió a detectar** en este snapshot, se evalúa su regla de cierre (3.4) con los datos de la ventana actual.

Como ahora, `statement_samples` se guarda después de los detectores.

### 3.4 Reglas de cierre

Un detalle importante: **solo AP-01 necesita una referencia anclada**. Es el único cuya regla se "autoajusta" y deja de disparar mientras el problema sigue. En los demás, la regla sigue disparando mientras el problema exista: AP-07 dispara mientras la consulta escriba a disco y AP-06 mientras la hija siga coincidiendo con el padre. Para ellos basta un cierre genérico.

| Patrón | Se cierra cuando... |
|---|---|
| AP-01 | **recuperado:** la media de la ventana vuelve a ≤ 1,5× la `baseline_median_ms` guardada al abrir (el margen evita que se abra y cierre por ruido); **nueva normalidad:** lleva 60 ventanas abierto |
| AP-06, AP-07, AP-08, AP-02, AP-03, AP-04 | **genérico:** la consulta se ejecutó en K ventanas seguidas (por ejemplo, K = 3) y la regla no disparó en ninguna |
| AP-05 | el índice tuvo lecturas o fue eliminado |
| Todos | **sin actividad:** la consulta no se ejecuta hace 24 h |

En la regla genérica, "la consulta se ejecutó" es importante: si la consulta no corrió, no se sabe si el problema sigue, así que no se cuenta.

## 4. Efecto sobre el informe y la validación

- **Las reglas de detección no cambian**, así que la Tabla 2 sigue igual.
- **M4 (latencia de detección):** se mide con `first_seen` menos la hora de inyección. Es lo mismo que hoy, pero sin ambigüedad.
- **Precisión y exhaustividad:** se cuentan **problemas** (filas con su `finding_key`), no filas por minuto. Es más fácil de contar que hoy.
- Habría que **declarar** los parámetros nuevos (1,5×, 60 ventanas, K = 3, 24 h) como parte del seguimiento, no de la detección.

## 5. Archivos a tocar

| Archivo | Cambio |
|---|---|
| `querylens_database/hallazgos.sql` | Columnas nuevas + índice único parcial; las filas existentes quedan `cerrado` |
| `detection_engine/models.py` | `Hallazgo.finding_key` y `Hallazgo.referencia` |
| `detection_engine/anti_patterns/*.py` | Cada detector arma su `finding_key` y su `referencia` |
| `detection_engine/storage.py` | `save_findings` pasa a abrir/actualizar; nueva función de cierre |
| `detection_engine/main.py` | Llamar al cierre después de los detectores |
| `api_rest/app/routers/diagnostics.py` + `schemas.py` | Devolver abiertos primero, con `first_seen`, `last_seen`, `resolved_at` |
| `frontend/components/FindingsPanel.tsx` + `types/database.ts` | Mostrar "abierto desde" y "resuelto (duración)" |
| `ql_sandbox/scenarios/run_scenario.py` | `findings_since` filtra por `first_seen` en vez de `detectado_en` |

## 6. Decisiones abiertas

1. **Valores de los parámetros:** margen de recuperación de AP-01 (1,5×), duración máxima (60 ventanas), K del cierre genérico (3) y tiempo sin actividad (24 h).
2. **Severidad:** ¿la máxima alcanzada o la de la última detección?
3. **Reapertura:** si un problema cerrado vuelve a disparar, ¿se abre uno nuevo o se reabre el anterior? Lo más simple es abrir uno nuevo.
4. **Hallazgos viejos:** ¿se migran agrupándolos por clave o se dejan como históricos cerrados?
5. **Alcance:** ¿empezar solo con AP-01, que es el que lo necesita para mostrar bien la degradación, o con todos a la vez?

## 7. Otras opciones consideradas para AP-01

| Opción | Qué hace | Por qué no se eligió |
|---|---|---|
| A. Dejarlo así | Avisa 5 ventanas y acepta el nuevo valor | Es la opción **vigente** para el MVP; solo hay que declararla en el informe |
| B. Excluir de la línea base las ventanas que dispararon | Congela la línea base mientras dure | Un cambio legítimo quedaría avisando para siempre |
| C. Línea base anclada con salida, dentro de la regla | Igual que esta propuesta, pero en el detector | Cambia la regla del informe y no sirve a otros patrones |
| D. Mediana de 30 ventanas | Deja de avisar a los 15 min en vez de 5 | Solo retrasa el problema y necesita 30 min de historia |
| E. Percentil 25 en vez de mediana | Deja de avisar a las ~8 ventanas | Solo lo retrasa y es más sensible a ventanas atípicas |
| G. Línea base por hora del día o promedio de decaimiento lento | Compara con "lo normal a esta hora" | Necesita días de historia; excede el MVP |
