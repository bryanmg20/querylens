"""
AP-02 escaneo completo evitable | casos

Verdad de referencia (expected): escaneo completo sobre una tabla grande
(>= 10.000 filas) cuyo predicado, con el valor REAL de la consulta, deja pasar
<= 10 % de la tabla y la tabla no tiene un indice que el motor use para ese
valor. Se define por el diseno de los datos (ver setup_postgres.sql), no por
el EXPLAIN de ninguna version.

Cada caso:
  id        nombre corto, unico en el patron
  expected  True si el antipatron esta presente
  table     tabla que identifica la consulta del caso en el snapshot
  sql       texto de la consulta, o funcion que lo arma (literal distinto por
            ejecucion); puede ser un dict {motor: ...} si difiere por motor
  nota      que se espera y por que alguna version podria fallar
"""

import random

RULE = "avoidable_full_scan"

CASES = [
    {
        "id": "status_raro",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": "SELECT id, amount FROM ql_ab_orders WHERE status = 'error'",
        "nota": (
            "0,1 % de la tabla sin indice. Con el literal real el plan estima ~1.000 filas; "
            "el plan generico de PG estima 1/n_distinct (~33 %) y podria no superar el umbral."
        ),
    },
    {
        "id": "status_comun",
        "expected": False,
        "table": "ql_ab_orders",
        "sql": "SELECT count(*), max(amount) FROM ql_ab_orders WHERE status = 'done'",
        "nota": "~97 % de la tabla: el escaneo completo es lo correcto.",
    },
    {
        "id": "rango_reciente",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": "SELECT id, amount FROM ql_ab_orders WHERE created_at >= '2026-10-16 00:00:00'",
        "nota": (
            "~0,5 % de la tabla sin indice. El plan generico de PG estima un rango con $1 en 1/3; "
            "MySQL sin indice ni histograma usa filtered constante (33 %) con cualquier literal."
        ),
    },
    {
        "id": "rango_amplio",
        "expected": False,
        "table": "ql_ab_orders",
        "sql": "SELECT count(*), max(amount) FROM ql_ab_orders WHERE created_at >= '2026-03-01 00:00:00'",
        "nota": "~80 % de la tabla: el escaneo completo es lo correcto.",
    },
    {
        "id": "valor_casi_unico",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": lambda: f"SELECT id, status FROM ql_ab_orders WHERE amount = {random.randint(0, 999999)}",
        "nota": "~1 fila por valor, sin indice: ambas versiones deberian estimar pocas filas.",
    },
    {
        "id": "region_20pct",
        "expected": False,
        "table": "ql_ab_orders",
        "sql": lambda: f"SELECT count(*), max(amount) FROM ql_ab_orders WHERE region = {random.randint(0, 4)}",
        "nota": (
            "20 % de la tabla: no es evitable. En MySQL sin indice ni histograma filtered es 10 % "
            "constante, asi que podria salir falso positivo en ambas versiones."
        ),
    },
    {
        "id": "indexado",
        "expected": False,
        "table": "ql_ab_orders",
        "sql": lambda: f"SELECT id, amount FROM ql_ab_orders WHERE customer_id = {random.randint(0, 99999)}",
        "nota": "Usa el indice de customer_id: no hay escaneo completo. Consulta rapida, a la version 1 le puede costar verla activa.",
    },
    {
        "id": "indexado_sesgado",
        "expected": False,
        "table": "ql_ab_orders",
        "sql": "SELECT id, amount FROM ql_ab_orders WHERE kind = 'error'",
        "nota": (
            "Con el literal real usa el indice de kind. El plan generico de PG estima ~33 % y podria "
            "elegir escaneo completo, pero esa selectividad no supera el umbral: no deberia dar hallazgo."
        ),
    },
    {
        "id": "tabla_chica",
        "expected": False,
        "table": "ql_ab_small",
        "sql": lambda: f"SELECT id FROM ql_ab_small WHERE code = {random.randint(0, 999)}",
        "nota": "1.000 filas, por debajo de DEFAULT_MIN_LIVE_ROWS: no aplica.",
    },
]
