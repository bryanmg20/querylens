"""
AP-02b escaneo completo evitable | formas de consulta mas complejas

Misma regla y misma verdad de referencia que ap02_avoidable_full_scan
(escaneo completo sobre una tabla grande cuyo predicado, con el valor REAL,
deja pasar <= 10 % de la tabla sin un indice que el motor use), pero con
consultas donde las filas que devuelve la consulta no son las filas que deja
pasar el escaneo: agregaciones, LIMIT, GROUP BY y joins. Solo PostgreSQL.

Por que importa para la version 3 (feature/pg-stats-explains): elige entre el
plan generico y las variantes de pg_stats comparando las filas estimadas de la
RAIZ del plan contra avg_rows_per_call. Si la raiz estima lo mismo en todos los
planes (count(*) = 1 fila, LIMIT 10 = 10 filas, GROUP BY = numero de grupos)
hay empate y queda el generico, aunque el escaneo de abajo estime muy distinto.

Mezclas: la misma consulta normalizada con varios valores en la carga. Llevan
expected=None porque falta definir que cuenta como antipatron cuando el valor
selectivo y el no selectivo se mezclan; el harness las informa como
detecta / no_detecta y no entran en las metricas. En la nota va la
selectividad promedio de la mezcla y la del valor selectivo.

Cada caso: ver ap02_avoidable_full_scan/cases.py.
"""

import random

RULE = "avoidable_full_scan"

JOIN = "FROM ql_ab_orders o JOIN ql_ab_customers c ON c.id = o.customer_id"


def mix(template: str, weights: dict[str, float]):
    """Consulta con un valor elegido por ejecucion segun los pesos."""
    values, probs = list(weights), list(weights.values())
    return lambda: template.format(random.choices(values, probs)[0])


CASES = [
    # --- agregaciones: la raiz estima 1 fila en todos los planes ---
    {
        "id": "agg_count_raro",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": "SELECT count(*) FROM ql_ab_orders WHERE status = 'error'",
        "nota": "0,1 % sin indice. Raiz = 1 fila en generico y variantes: empate, queda el generico (~33 %).",
    },
    {
        "id": "agg_sum_reciente",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": "SELECT sum(amount) FROM ql_ab_orders WHERE created_at >= '2026-10-16 00:00:00'",
        "nota": "~0,5 % sin indice, rango. Raiz = 1 fila: empate, queda el generico.",
    },
    # --- LIMIT: la raiz estima el LIMIT en todos los planes ---
    {
        "id": "limit_raro",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": "SELECT id, amount FROM ql_ab_orders WHERE status = 'error' LIMIT 10",
        "nota": (
            "0,1 % sin indice; el escaneo lee ~10.000 filas hasta juntar 10. Raiz = 10 filas: empate. "
            "Consulta corta: a la version 1 le puede costar verla activa."
        ),
    },
    {
        "id": "limit_comun",
        "expected": False,
        "table": "ql_ab_orders",
        "sql": "SELECT id, amount FROM ql_ab_orders WHERE status = 'done' LIMIT 10",
        "nota": "~97 % de la tabla: el escaneo completo es lo correcto.",
    },
    # --- GROUP BY: la raiz estima el numero de grupos ---
    {
        "id": "group_by_raro",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": "SELECT region, count(*) FROM ql_ab_orders WHERE status = 'error' GROUP BY region",
        "nota": "0,1 % sin indice. Raiz = 5 grupos en todos los planes: empate, queda el generico.",
    },
    # --- joins ---
    {
        "id": "join_raro",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": f"SELECT o.id, c.name {JOIN} WHERE o.status = 'error'",
        "nota": "Filtro selectivo en ql_ab_orders (0,1 %); la raiz devuelve ~1.000 filas, igual que el escaneo.",
    },
    {
        "id": "join_comun",
        "expected": False,
        "table": "ql_ab_orders",
        "sql": f"SELECT count(*) {JOIN} WHERE o.status = 'done'",
        "nota": "~97 % de ql_ab_orders y ql_ab_customers entera: ningun escaneo es evitable.",
    },
    {
        "id": "join_filtro_dim",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": f"SELECT o.id, o.amount {JOIN} WHERE c.segment = 'vip'",
        "nota": (
            "Filtro selectivo en ql_ab_customers (0,1 %, 100 filas) sin indice; la raiz devuelve ~1.000 "
            "ordenes, no las 100 filas del escaneo de clientes."
        ),
    },
    {
        "id": "join_dos_filtros",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": f"SELECT o.id, c.name {JOIN} WHERE o.status = 'error' AND c.segment = 'vip'",
        "nota": "Dos parametros en dos tablas, ambos 0,1 % sin indice. La raiz devuelve ~1 fila.",
    },
    {
        "id": "join_group_by_raro",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": f"SELECT c.segment, count(*) {JOIN} WHERE o.status = 'error' GROUP BY c.segment",
        "nota": "0,1 % en ql_ab_orders; la raiz estima 3 grupos en todos los planes: empate.",
    },
    # --- mezclas de valores en la misma consulta (expected sin definir) ---
    {
        "id": "mezcla_error_done_99_1",
        "expected": None,
        "table": "ql_ab_orders",
        "sql": mix("SELECT id, amount FROM ql_ab_orders WHERE status = '{}'", {"error": 99, "done": 1}),
        "nota": "99 % 'error' (0,1 %) / 1 % 'done' (97 %). Promedio ~1,1 % de la tabla.",
    },
    {
        "id": "mezcla_error_done_90_10",
        "expected": None,
        "table": "ql_ab_orders",
        "sql": mix("SELECT id, amount FROM ql_ab_orders WHERE status = '{}'", {"error": 90, "done": 10}),
        "nota": "90 % 'error' / 10 % 'done'. Promedio ~9,8 % de la tabla, justo debajo del umbral.",
    },
    {
        "id": "mezcla_error_done_50_50",
        "expected": None,
        "table": "ql_ab_orders",
        "sql": mix("SELECT id, amount FROM ql_ab_orders WHERE status = '{}'", {"error": 50, "done": 50}),
        "nota": "50 % 'error' / 50 % 'done'. Promedio ~49 % de la tabla.",
    },
    {
        "id": "mezcla_error_done_10_90",
        "expected": None,
        "table": "ql_ab_orders",
        "sql": mix("SELECT id, amount FROM ql_ab_orders WHERE status = '{}'", {"error": 10, "done": 90}),
        "nota": "10 % 'error' / 90 % 'done'. Promedio ~87 % de la tabla.",
    },
    {
        "id": "mezcla_error_pending_50_50",
        "expected": True,
        "table": "ql_ab_orders",
        "sql": mix("SELECT id, amount FROM ql_ab_orders WHERE status = '{}'", {"error": 50, "pending": 50}),
        "nota": (
            "Los dos valores son selectivos (0,1 % y ~2,9 %): es antipatron con cualquier definicion "
            "de mezcla. Promedio ~1,5 %."
        ),
    },
]
