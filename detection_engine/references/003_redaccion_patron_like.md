# Redacción del patrón de LIKE (pedido del detector)

Pedido desde `detection_engine` para AP-04 índice ausente (y, a futuro, AP-03 predicado no sargable). Es un cambio solo de redacción en `stages/normalize.py`; no cambia el contrato.

## Qué pasa hoy

La redacción N-1 reemplaza el patrón completo de un `LIKE`, así que el detector no sabe si empezaba con comodín:

| Motor | Plan crudo | Lo que llega al detector |
|---|---|---|
| Postgres (auto_explain) | `(note ~~ '%99'::text)` | `(note ~~ $1)` |
| Postgres (auto_explain) | `(note ~~ 'nota1%'::text)` | `(note ~~ $1)` |
| MySQL (muestra) | `` (`note` like '%99') `` | `(ql_pt_orders.note LIKE '$1')` |
| MySQL (muestra) | `` (`note` like 'nota1%') `` | `(ql_pt_orders.note LIKE '$1')` |

(Casos 09 y 10 de `ql_sandbox/explain_predicados/`, corrida del 2026-10-13.)

Para AP-04 la diferencia importa: con `LIKE 'nota1%'` un índice sobre `note` sirve (busca el prefijo), con `LIKE '%99'` no. Como no se puede distinguir, el detector dejó de contar el `LIKE` en los dos motores (antes en MySQL lo contaba siempre y podía recomendar un índice inútil para `'%99'`).

## Qué se pide

Al redactar un literal que es el **patrón de un LIKE/ILIKE** (`~~`, `~~*`, `!~~` en Postgres), conservar los comodines de los **extremos** y reemplazar solo lo del medio:

| Patrón real | Redactado hoy | Redactado pedido |
|---|---|---|
| `'nota1%'` | `'$1'` / `$1` | `'$1%'` |
| `'%99'` | `'$1'` / `$1` | `'%$1'` |
| `'%abc%'` | `'$1'` / `$1` | `'%$1%'` |
| `'_bc'` | `'$1'` / `$1` | `'_$1'` |
| `'abc'` (sin comodines) | `'$1'` / `$1` | `'$1'` |

Basta con el primer y el último carácter si son `%` o `_`. No viaja ningún dato: solo si el patrón empieza o termina con comodín.

Dónde:
- **MySQL:** `clean_mysql_explain_predicate_dynamic`, en el camino sqlglot (el `Literal` string que es `expression` de un `exp.Like`/`exp.ILike`) y en el respaldo con regex (`replace_literal`).
- **Postgres:** `redact_postgres_plan_predicate`. Ojo con la nota de su docstring: hoy el literal de auto_explain se reduce a `$n` para que coincida con el plan genérico (`(c ~~ $1)`). Con este cambio dejarían de coincidir en los `LIKE` (`'%$1'::text` contra `$1`); hay que decidir si eso es aceptable. El plan genérico no tiene el valor, así que ahí no hay nada que conservar.

Comprobado del lado del detector: sqlglot (dialecto postgres) lee `LIKE '%$1'`, `LIKE '$1%'`, `LIKE '%$1%'` y `~~ '%$1'::text` como `Like` con el literal intacto. Con la redacción pedida, el detector vuelve a contar el `LIKE` cuando el patrón no empieza con `%` ni `_`.

## Si se hace, actualizar

- La decisión N-1 de privacidad en `004_decisiones_contrato.md` (qué parte del literal se conserva).
- Avisar al detector: hoy `'$1'` en MySQL significa "no se sabe dónde estaba el comodín"; después del cambio significaría "no tiene comodines". Hay que activar el `LIKE` en AP-04 al mismo tiempo que se despliega la redacción nueva.
