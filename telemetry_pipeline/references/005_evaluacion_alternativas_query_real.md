# 9. Evaluación de alternativas

Expone las alternativas tecnológicas o arquitectónicas consideradas, los criterios de comparación utilizados y la justificación de la opción seleccionada.

Para diagnosticar una consulta lenta hace falta conocer su texto completo tal y como se ejecutó, porque los datos que se recogen están resumidos. Se compararon dos maneras de conseguir ese texto:

- **Opción A — Mirar la actividad del momento.** En el instante del análisis se pregunta a la base de datos qué consultas están en marcha. La consulta se encuentra solo si sigue ejecutándose justo en ese instante.
- **Opción B — Leer los registros (logs) del servidor.** El servidor deja por escrito cada consulta que ejecuta. Se leen los registros más recientes, se buscan las consultas que interesan y se toma su texto completo desde ahí, aunque ya hayan terminado.

## Resultado de las pruebas

### Prueba 1 — Comparación directa

Se probaron las dos opciones sobre el mismo banco, varias veces y con tráfico de relleno después de las consultas pesadas.

| Motor | Consultas analizadas | Opción A | Opción B |
|-------|----------------------|----------|----------|
| PostgreSQL | 30 | 0 % | 100 % |
| MySQL | 30 | 0 % | 100 % |

**Por qué la Opción A da 0 %.** La actividad solo muestra lo que se está ejecutando en el momento exacto de la revisión. Las consultas pesadas de la prueba terminan muy rápido (en milisegundos), así que cuando se mira, ya no queda nada en marcha. Solo la encuentra si la consulta sigue viva en ese instante.

**Por qué se usó entonces.** Fue la primera versión: lo más rápido de construir, sin configurar ni leer logs, y válido para las pruebas de diseño donde se puede dejar una consulta larga abierta a propósito. No era sólido para producción: las consultas cortas, que son la mayoría del tráfico real, nunca se encuentran.

### Prueba 2 — Límite de la Opción B (cuándo empieza a perder)

El 100 % de la Opción B depende de cuánto registro se lee: siempre se lee el **final** del log, una cantidad fija de líneas (200 000 por defecto). Si después de las consultas pesadas se escribe mucho tráfico nuevo, ese tráfico queda al final y las consultas pesadas pueden quedar **antes de lo leído** y perderse. La prueba varió el tamaño de lo que se lee y el volumen de tráfico posterior:

| Líneas leídas del log | Tráfico posterior | MySQL | PostgreSQL |
|-----------------------|-------------------|-------|------------|
| 200 000 | ninguno | 100 % | 100 % |
| 200 000 | mucho | 100 % | 100 % |
| 1 000 | mucho | 50 % | 10 % |
| 200 | mucho | 10 % | 15 % |
| 200 | ninguno | 10 % | 100 % |
| 50 | ninguno | 0 % | 0 % |

Conclusión: la Opción B solo falla cuando el tráfico reciente es mayor que la cantidad de log que se lee. En producción, esa cantidad debe ser lo bastante grande para cubrir el tráfico que se espera en una ventana de tiempo, o las consultas pesadas más antiguas se pierden.

## Pregunta 1. ¿Cuál alternativa ofrece mejor desempeño bajo carga esperada?

**Criterios de comparación:**

- **Latencia promedio y máxima:** tiempo de respuesta de operaciones críticas.
- **Throughput (capacidad de procesamiento):** número de solicitudes que el sistema puede manejar por unidad de tiempo.
- **Comportamiento bajo carga concurrente:** degradación del sistema cuando aumenta el número de usuarios simultáneos.

| Criterio | Opción A | Opción B |
|----------|----------|----------|
| **Latencia promedio y máxima** | Casi ninguna: solo compara datos que ya estaban en memoria, sin trabajo extra. | Baja y acotada: lee una parte fija del log, no todo el historial; el trabajo extra no crece con el tiempo de vida del log. |
| **Throughput (capacidad de procesamiento)** | No añade casi coste, pero ese ahorro no sirve: bajo la carga esperada (muchas consultas cortas) encuentra 0 % de ellas. | Soporta el tráfico esperado siempre que la cantidad de log leída alcance a cubrir el volumen de consultas ejecutadas. |
| **Comportamiento bajo carga concurrente** | Solo ve lo que esté en marcha al mirar; con muchos usuarios atrapa algunas consultas largas, pero las cortas y ya terminadas se pierden por diseño. | No depende de cuántos usuarios haya ni de cuánto duren sus consultas: cada consulta ejecutada queda registrada por escrito. |

**Conclusión:** la Opción B funciona bajo la carga esperada; la Opción A, aunque casi no cuesta, no cumple su función con esa carga.

## Pregunta 2. ¿Qué grado de acoplamiento introduce cada opción?

**Criterios de comparación:**

- **Dependencia de servicios externos:** nivel en que el sistema depende de plataformas como APIs externas.
- **Interdependencia entre módulos internos:** qué tanto un cambio en un módulo afecta a otros.
- **Facilidad de sustitución de componentes:** capacidad de reemplazar una tecnología (ej: backend) sin rediseñar todo el sistema.

| Criterio | Opción A | Opción B |
|----------|----------|----------|
| **Dependencia de servicios externos** | Ninguna nueva: usa información de la propia base de datos que ya se consultaba. | Mayor: requiere que el servidor tenga activos y accesibles sus registros (ruta y formato según el motor). |
| **Interdependencia entre módulos internos** | Fuerte con el propio mecanismo de recolección: depende de que la consulta aparezca a la vez en dos listas internas. | Contenida: vive en módulos dedicados a leer e interpretar los registros; el resto del proceso no depende del formato de cada motor. |
| **Facilidad de sustitución de componentes** | Muy alta: es una simple comparación, reemplazable en minutos. | Media: cambiarla requeriría reescribir el lector por motor, pero está separado del resto. |

**Conclusión:** la Opción A acopla menos, pero ese bajo acoplamiento es justamente lo que la vuelve inútil: queda atada a un instante de la actividad y no tiene memoria. La Opción B añade dependencia de los registros del servidor, pero queda contenida en módulos propios y no obligó a rediseñar el resto.

## Pregunta 3. ¿Qué nivel de disponibilidad y tolerancia a fallos ofrece cada alternativa?

**Criterios de comparación:**

- **Tiempo de disponibilidad (uptime esperado):** porcentaje de tiempo en que el sistema está operativo.
- **Mecanismos de recuperación ante fallos:** existencia de redundancia, backups o reintentos automáticos.
- **Impacto de fallos parciales:** qué ocurre si un componente falla (¿cae todo el sistema o solo una parte?).

| Criterio | Opción A | Opción B |
|----------|----------|----------|
| **Tiempo de disponibilidad** | Bajo: solo "funciona" si la consulta pesada sigue en marcha justo cuando se mira, algo casi imposible con consultas cortas. | Alto: los registros guardan lo ejecutado recientemente, así que la cobertura es la historia reciente completa y no un solo instante. |
| **Mecanismos de recuperación ante fallos** | Ninguno: si la consulta no está en marcha, simplemente no aparece, sin aviso ni reintento. | Varios: límite de líneas leídas (no reprocesa todo el historial), filtro por tiempo, salto de registros incompletos y aviso si el archivo de registro no existe. |
| **Impacto de fallos parciales** | Falla en silencio: si la base no muestra actividad o no hay permisos, el informe queda incompleto sin que se note la causa. | Controlado: si el registro no está disponible, se avisa y el proceso continúa; una consulta no encontrada no afecta a las demás. |

**Conclusión:** la Opción B ofrece disponibilidad y tolerancia a fallos; la Opción A no tiene manera de recuperarse y sus fallos pasan desapercibidos.

## Opción seleccionada y justificación

Se elige la **Opción B (registros del servidor)**:

- Encuentra el texto completo de las consultas aunque ya hayan terminado, lo que cubre el caso típico de una consulta que se degradó y volvió a estar bien.
- Mantiene un límite claro de trabajo extra: solo lee una ventana fija del registro.
- Si algo falla con los registros, avisa y sigue, en vez de entregar un informe incompleto en silencio.
- Separó el lector de registros en módulos propios, sin tocar el resto del diseño.

La **Opción A queda descartada para producción**, pero se conserva documentada como la versión rápida de pruebas.

## Evidencia de las pruebas

| Prueba | Script | Resultado guardado |
|--------|--------|--------------------|
| Comparación directa A vs B | `compare_real_query_mechanics.py` | `telemetry_pipeline/results/compare_real_query_mechanics.{json,txt}` |
| Límite de la ventana de registros | `ablate_recovery_window.py` | `telemetry_pipeline/results/ablate_recovery_window.{json,txt}` |

Para repetir:

```
venv\Scripts\python.exe compare_real_query_mechanics.py [MUESTRAS] [REPS] [RUIDO]
venv\Scripts\python.exe ablate_recovery_window.py [MUESTRAS] [REPS]
```