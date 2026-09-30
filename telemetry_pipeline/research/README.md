# research

Scripts de experimento archivados. **No forman parte del pipeline** y no se mantienen.

Se conservan por registro de la evaluación metodológica que llevó a eliminar
el backend de logs. Los commits `7bb7a6e`, `d3e2c2b` y `ee95fb9` contienen los
resultados medidos.

## Por qué se descartaron los logs

La recuperación de texto real de consulta se apoyaba en dos mecanismos:

- **PostgreSQL** — parsing de `csvlog` con `log_statement=all`,
  `log_min_duration_statement=0` y bind parameters materializados.
- **MySQL** — parsing del slow log, que solo captura consultas que superan
  `long_query_time`.

Ambos quedaron fuera por los siguientes motivos, verificados contra
`postgres:17` y `mysql:8.0`:

1. **Cobertura incompleta.** El slow log de MySQL solo registra consultas por
   encima de `long_query_time`. Una consulta de 5 ms que se ejecuta 200000
   veces no aparece nunca, aunque sea la de mayor impacto agregado.
2. **Requiere permisos del sistema de archivos.** Hay que leer el directorio de
   logs del contenedor, lo que obliga a montar volúmenes o a operar con
   privilegios sobre el host. Rompe el aislamiento por instancia.
3. **Requiere configuración del servidor.** `logging_collector`, `csvlog`,
   `log_min_duration_statement` y `long_query_time` hay que habilitarlos del
   lado del motor. Un cliente que no controla su PostgreSQL no puede.
4. **El texto logueado no es el texto planificado.** El log registra la
   ejecución; el plan se calcula sobre otra forma de la consulta.

## Mecanismos que lo reemplazan

| Motor  | Fuente                        | Cómo se explica                          |
| ------ | ----------------------------- | ---------------------------------------- |
| Postgres | `pg_stat_statements.query`  | `EXPLAIN (GENERIC_PLAN)` (PG16+)         |
| MySQL    | `QUERY_SAMPLE_TEXT`           | `EXPLAIN FORMAT=JSON` con valores reales |

La asimetría es propia de cada motor. PostgreSQL entrega el texto normalizado
con placeholders `$1` y resuelve el plan genérico sin conocer los valores.
MySQL no tiene equivalente a `EXPLAIN (GENERIC_PLAN)`, así que necesita una
consulta con valores reales para decidir el plan, y esa columna es
`QUERY_SAMPLE_TEXT` (`DIGEST_TEXT` lleva `?` y no produce plan).

## Archivos

| Script | Qué midió |
| ------ | --------- |
| `ablate_recovery_window.py` | Ventana de recuperación y su efecto en cobertura |
| `compare_real_query_mechanics.py` | Comparación activity-table contra log-based |
| `run_battery_samples.py` | Batería de consultas contra el pipeline completo |
| `compare_queue.py` | Comparación de payloads encolados |

Los cuatro importan `stages.log_reader` y `config.logs`, que ya no existen.
No se van a portar.
