"""Casos de la medicion de AP-07, AP-01 y AP-06 (ver README.md).

Cada medicion define, por motor:
  - setup():  sentencias que arman sus tablas (prefijo ql_m07_ / ql_m01_ / ql_m06_);
  - cases():  la lista de casos. Un caso tiene un rol (positivo, control o extra),
              una categoria para el reporte, las consultas que lo identifican
              (`queries`: nombre -> SQL de muestra con literales) y `run`, que
              ejecuta una vuelta del caso con valores al azar;
  - inject(): solo AP-01, la degradacion que se aplica a mitad de la corrida;
  - teardown().

Las consultas llevan siempre literales (nada de parametros del servidor): asi
las deja la aplicacion y asi se normalizan en pg_stat_statements / el digest de
MySQL. El valor de los literales no cambia el query_id; la forma si, y cada caso
tiene una forma propia (se verifica en driver.py antes de correr).
"""

import random
from dataclasses import dataclass, field
from typing import Callable

ROLES = ("positivo", "control", "extra")


@dataclass
class Case:
    cid: str
    role: str
    category: str
    # nombre -> SQL de muestra; la consulta que el detector deberia marcar es "target"
    queries: dict[str, str]
    # ejecuta una vuelta del caso: run(cursor, rng)
    run: Callable
    # sentencias de sesion antes de cada vuelta (AP-07: memoria de trabajo)
    session: list[str] = field(default_factory=list)
    # vueltas por ronda (AP-06) o 1
    per_round: int = 1
    meta: dict = field(default_factory=dict)


def _fetch(cur, sql):
    cur.execute(sql)
    return cur.fetchall() if cur.description else []


# =====================================================================
# AP-07 vertido a disco
# =====================================================================

# MySQL con tablas mas grandes: con la configuracion del informe (TempTable,
# temptable_use_mmap = OFF) la tabla temporal solo va a disco si no cabe en los
# 2 MB de temptable_max_ram del sandbox; con 30.000-50.000 filas cabe y no derrama
M07_SIZES_BY_ENGINE = {
    "postgres": [30_000, 50_000, 80_000, 120_000, 160_000, 200_000],
    "mysql": [100_000, 150_000, 200_000, 300_000, 400_000, 500_000],
}
M07_TABLES = [f"ql_m07_t{i}" for i in range(6)]


def m07_setup(engine):
    stmts = []
    if engine == "mysql":
        stmts.append("SET SESSION cte_max_recursion_depth = 1000000")
    for table, n in zip(M07_TABLES, M07_SIZES_BY_ENGINE[engine]):
        stmts.append(f"DROP TABLE IF EXISTS {table}")
        if engine == "postgres":
            stmts += [
                f"""CREATE TABLE {table} (id int PRIMARY KEY, g_hi int NOT NULL, g_mid int NOT NULL,
                    g_lo int NOT NULL, s varchar(20) NOT NULL, d date NOT NULL, amt numeric(10,2) NOT NULL,
                    fk int NOT NULL, pad char(40) NOT NULL DEFAULT 'x')""",
                f"""INSERT INTO {table} (id, g_hi, g_mid, g_lo, s, d, amt, fk)
                    SELECT g, floor(random() * {n})::int, floor(random() * {n // 5})::int,
                           floor(random() * 100)::int, 'v' || floor(random() * {n // 2})::int,
                           date '2020-01-01' + floor(random() * 3000)::int,
                           round((random() * 1000)::numeric, 2), floor(random() * {n})::int
                    FROM generate_series(1, {n}) g""",
                f"ANALYZE {table}",
            ]
        else:
            stmts += [
                f"""CREATE TABLE {table} (id int PRIMARY KEY, g_hi int NOT NULL, g_mid int NOT NULL,
                    g_lo int NOT NULL, s varchar(20) NOT NULL, d date NOT NULL, amt decimal(10,2) NOT NULL,
                    fk int NOT NULL, pad char(40) NOT NULL DEFAULT 'x')""",
                f"""INSERT INTO {table} (id, g_hi, g_mid, g_lo, s, d, amt, fk)
                    WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < {n})
                    SELECT n, FLOOR(RAND() * {n}), FLOOR(RAND() * {n // 5}), FLOOR(RAND() * 100),
                           CONCAT('v', FLOOR(RAND() * {n // 2})),
                           DATE_ADD('2020-01-01', INTERVAL FLOOR(RAND() * 3000) DAY),
                           ROUND(RAND() * 1000, 2), FLOOR(RAND() * {n})
                    FROM g""",
                f"ANALYZE TABLE {table}",
            ]
    return stmts


def m07_teardown(engine):
    return [f"DROP TABLE IF EXISTS {t}" for t in M07_TABLES]


# (codigo, categoria, motores, SQL). {T} tabla, {U} otra tabla, {H} mitad de filas.
M07_POSITIVE_TEMPLATES = [
    ("G1", "GROUP BY", "both", "SELECT g_hi, COUNT(*) FROM {T} GROUP BY g_hi HAVING COUNT(*) > 4"),
    ("G2", "GROUP BY", "both", "SELECT g_mid, SUM(amt) FROM {T} GROUP BY g_mid HAVING SUM(amt) > 9000"),
    ("G3", "GROUP BY", "both", "SELECT s, COUNT(*) FROM {T} GROUP BY s HAVING COUNT(*) > 6"),
    ("G4", "GROUP BY", "both", "SELECT g_hi, g_lo, MAX(amt) FROM {T} GROUP BY g_hi, g_lo HAVING MAX(amt) > 999"),
    ("G5", "GROUP BY", "both", "SELECT d, g_mid, COUNT(*) FROM {T} GROUP BY d, g_mid HAVING COUNT(*) > 2"),
    ("G6", "GROUP BY", "both", "SELECT fk % 40000, AVG(amt) FROM {T} GROUP BY fk % 40000 HAVING AVG(amt) > 995"),
    ("G7", "GROUP BY + ORDER BY", "both",
     "SELECT g_hi, COUNT(*) FROM {T} GROUP BY g_hi ORDER BY COUNT(*) DESC, g_hi LIMIT 5"),
    ("D1", "DISTINCT", "both", "SELECT COUNT(*) FROM (SELECT DISTINCT g_hi FROM {T}) x"),
    ("D2", "DISTINCT", "both", "SELECT COUNT(*) FROM (SELECT DISTINCT s, g_lo FROM {T}) x"),
    ("D3", "COUNT(DISTINCT)", "both", "SELECT COUNT(DISTINCT fk) FROM {T}"),
    ("D4", "COUNT(DISTINCT)", "both", "SELECT g_lo, COUNT(DISTINCT s) FROM {T} GROUP BY g_lo"),
    ("S1", "ORDER BY grande", "both", "SELECT id FROM {T} ORDER BY g_hi, id LIMIT 1 OFFSET {H}"),
    ("S2", "ORDER BY grande", "both", "SELECT id FROM {T} ORDER BY s DESC, id LIMIT 1 OFFSET {H}"),
    ("S3", "ORDER BY grande", "both", "SELECT id, amt FROM {T} ORDER BY amt, fk LIMIT 1 OFFSET {H}"),
    ("W1", "ventana", "both",
     "SELECT COUNT(*) FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY g_mid ORDER BY amt) AS rn FROM {T}) x WHERE rn = 1"),
    ("W2", "ventana", "both", "SELECT MAX(r) FROM (SELECT RANK() OVER (ORDER BY amt, id) AS r FROM {T}) x"),
    ("W3", "ventana", "both",
     "SELECT COUNT(*) FROM (SELECT g_hi, SUM(amt) OVER (PARTITION BY g_hi) AS t FROM {T}) x WHERE t > 1500"),
    ("J1", "hash join", "both", "SELECT COUNT(*) FROM {T} a JOIN {U} b ON b.fk = a.g_hi"),
    ("U1", "UNION / EXCEPT", "both", "SELECT COUNT(*) FROM (SELECT g_hi FROM {T} UNION SELECT fk FROM {T}) x"),
    ("U2", "UNION / EXCEPT", "both", "SELECT COUNT(*) FROM (SELECT g_hi FROM {T} EXCEPT SELECT fk FROM {U}) x"),
    ("C1", "CTE / derivada", "postgres",
     "WITH c AS MATERIALIZED (SELECT g_hi, COUNT(*) AS n FROM {T} GROUP BY g_hi) SELECT COUNT(*) FROM c WHERE n > 2"),
    ("C1", "CTE / derivada", "mysql",
     "WITH c AS (SELECT g_hi, COUNT(*) AS n FROM {T} GROUP BY g_hi) SELECT COUNT(*) FROM c WHERE n > 2"),
    ("C2", "CTE / derivada", "both",
     "SELECT COUNT(*) FROM (SELECT fk, MIN(d) AS m FROM {T} GROUP BY fk) x WHERE m > '2027-01-01'"),
    ("Q1", "subconsulta IN", "both", "SELECT COUNT(*) FROM {T} WHERE g_hi IN (SELECT fk FROM {U} WHERE g_lo < 50)"),
    ("R1", "ROLLUP", "postgres",
     "SELECT g_mid, g_lo, COUNT(*) FROM {T} GROUP BY ROLLUP (g_mid, g_lo) HAVING COUNT(*) > 40"),
    ("R1", "ROLLUP", "mysql",
     "SELECT g_mid, g_lo, COUNT(*) FROM {T} GROUP BY g_mid, g_lo WITH ROLLUP HAVING COUNT(*) > 40"),
    ("A1", "agregado ordenado", "postgres",
     "SELECT g_lo, percentile_cont(0.5) WITHIN GROUP (ORDER BY amt) FROM {T} GROUP BY g_lo"),
    ("A2", "agregado ordenado", "postgres",
     "SELECT g_lo, LENGTH(STRING_AGG(s, ',' ORDER BY s)) FROM {T} GROUP BY g_lo"),
    ("A2", "agregado ordenado", "mysql", "SELECT g_lo, LENGTH(GROUP_CONCAT(s ORDER BY s)) FROM {T} GROUP BY g_lo"),
]

# consultas sanas: resultados chicos o memoria suficiente
M07_CONTROL_TEMPLATES = [
    ("K1", "GROUP BY chico", "both", "SELECT g_lo, COUNT(*) FROM {T} GROUP BY g_lo"),
    ("K2", "filtro + GROUP BY", "both", "SELECT g_lo, AVG(amt) FROM {T} WHERE id < 3000 GROUP BY g_lo"),
    ("K3", "DISTINCT chico", "both", "SELECT DISTINCT g_lo FROM {T} WHERE g_lo < 30"),
    ("K4", "top-N", "both", "SELECT id FROM {T} WHERE id BETWEEN 1 AND 2000 ORDER BY amt LIMIT 10"),
    ("K5", "conteo", "both", "SELECT COUNT(*) FROM {T} WHERE g_lo = 7"),
    ("K6", "orden por indice", "both", "SELECT id, amt FROM {T} ORDER BY id DESC LIMIT 20"),
    # Postgres: el mismo tipo de agregado que los positivos, con work_mem de sobra
    ("K7", "GROUP BY grande con memoria", "postgres", "SELECT g_mid, COUNT(*) FROM {T} GROUP BY g_mid HAVING COUNT(*) > 9"),
    ("K7", "GROUP BY chico", "mysql", "SELECT g_lo, MIN(d), MAX(d) FROM {T} GROUP BY g_lo"),
]

# memoria de trabajo de la sesion de cada positivo (va rotando por tabla): con
# la mas grande, los casos chicos quedan al borde y algunos no derraman
M07_PG_WORK_MEM = ["64kB", "128kB", "256kB"]


def _m07_render(template, i, size):
    return template.format(T=M07_TABLES[i], U=M07_TABLES[(i + 1) % len(M07_TABLES)], H=size // 2)


def _m07_session(engine, role, i):
    if engine == "postgres":
        return [f"SET work_mem = '{M07_PG_WORK_MEM[i % 3] if role != 'control' else '64MB'}'"]
    # Configuracion del escenario de AP-07 en el informe (Tabla 2): sort_buffer_size
    # reducido en la sesion y temptable_use_mmap = OFF en el servidor (measure.py),
    # con el motor por defecto de las tablas temporales (TempTable)
    if role == "control":
        return ["SET SESSION sort_buffer_size = DEFAULT"]
    return ["SET SESSION sort_buffer_size = 32768"]


def _single_query_run(sql):
    def run(cur, rng):
        _fetch(cur, sql)
    return run


def m07_pool(engine):
    """Todos los candidatos (positivos y controles posibles). driver.py los calibra
    y elige los 100 positivos que de verdad derraman y 40 controles que no."""
    pool = []
    for templates, role in ((M07_POSITIVE_TEMPLATES, "positivo"), (M07_CONTROL_TEMPLATES, "control")):
        for code, category, engines, template in templates:
            if engines not in ("both", engine):
                continue
            for i, size in enumerate(M07_SIZES_BY_ENGINE[engine]):
                sql = _m07_render(template, i, size)
                pool.append(Case(
                    cid=f"{code}-t{i}",
                    role=role,
                    category=category,
                    queries={"target": sql},
                    run=_single_query_run(sql),
                    session=_m07_session(engine, role, i),
                    meta={"template": code, "table": M07_TABLES[i], "rows": size,
                          "memoria": "; ".join(_m07_session(engine, role, i))},
                ))
    return pool


# =====================================================================
# AP-01 degradacion respecto de la linea base
# =====================================================================

M01_TABLES = 10
M01_COLS = 14
M01_ROWS = 60_000
M01_HOT = 25          # valores "calientes" de los casos de crecimiento de datos
M01_GROWTH_ROWS = 40_000
M01_LO = 20           # cardinalidad de lo (indice menos selectivo)


def _m01_t(i):
    return f"ql_m01_t{i}"


def _m01_assignment(engine):
    """(tabla, columna) -> categoria, fijo por semilla. Tablas 0-7: 100 positivos
    y 12 controles que comparten tabla con los degradados; tablas 8-9: 28
    controles en tablas que no se tocan."""
    combos = [(i, j) for i in range(8) for j in range(M01_COLS)]
    random.Random(7).shuffle(combos)
    out = {}
    for n, combo in enumerate(combos):
        if n < 25:
            cat = "indice menos selectivo"
        elif n < 50:
            cat = "crecimiento de datos"
        elif n < 100:
            # en MySQL, 15 de los 50 se ocultan (INVISIBLE) en vez de borrarse
            cat = "indice invisible" if engine == "mysql" and n >= 85 else "indice eliminado"
        else:
            cat = "control en tabla degradada"
        out[combo] = cat
    for i in (8, 9):
        for j in range(M01_COLS):
            out[(i, j)] = "control en tabla sin cambios"
    return out


M01_SHAPES = {
    "eq": lambda t, c: (f"SELECT id, pad FROM {t} WHERE {c} = {{v}}",),
    "range": lambda t, c: (f"SELECT id FROM {t} WHERE {c} BETWEEN {{v}} AND {{v30}}",),
    "order_limit": lambda t, c: (f"SELECT id, {c} FROM {t} WHERE {c} > {{v}} ORDER BY {c} LIMIT 5",),
    "count": lambda t, c: (f"SELECT COUNT(*) FROM {t} WHERE {c} = {{v}}",),
    "in5": lambda t, c: (f"SELECT id FROM {t} WHERE {c} IN ({{a}}, {{b}}, {{c}}, {{d}}, {{e}})",),
    "join": lambda t, c: (f"SELECT x.id, d.name FROM {t} x JOIN ql_m01_dim d ON d.id = x.lo WHERE x.{c} = {{v}}",),
    "max": lambda t, c: (f"SELECT MAX(id) FROM {t} WHERE {c} = {{v}}",),
    "exists": lambda t, c: (f"SELECT 1 FROM {t} WHERE {c} = {{v}} LIMIT 1",),
}
M01_LESS_SELECTIVE_SHAPES = {
    "lo_eq": lambda t, c: (f"SELECT id, pad FROM {t} WHERE lo = {{w}} AND {c} = {{v}}",),
    "lo_count": lambda t, c: (f"SELECT COUNT(*) FROM {t} WHERE lo = {{w}} AND {c} = {{v}}",),
    "lo_range": lambda t, c: (f"SELECT id FROM {t} WHERE lo = {{w}} AND {c} BETWEEN {{v}} AND {{v20}}",),
}
M01_GROWTH_SHAPES = {
    "hot_eq": lambda t, c: (f"SELECT id, pad FROM {t} WHERE {c} = {{h}}",),
    "hot_max": lambda t, c: (f"SELECT MAX(pad) FROM {t} WHERE {c} = {{h}}",),
    "hot_order": lambda t, c: (f"SELECT id FROM {t} WHERE {c} = {{h}} ORDER BY pad LIMIT 3",),
}


def _m01_values(rng):
    v = rng.randrange(0, M01_ROWS - 50)
    a, b, c, d, e = (rng.randrange(M01_ROWS) for _ in range(5))
    return {"v": v, "v30": v + 30, "v20": v + 20, "w": rng.randrange(M01_LO), "h": rng.randrange(M01_HOT),
            "a": a, "b": b, "c": c, "d": d, "e": e}


def m01_cases(engine):
    assignment = _m01_assignment(engine)
    cases = []
    counters = {}
    for (i, j), cat in sorted(assignment.items()):
        t, c = _m01_t(i), f"c{j}"
        n = counters[cat] = counters.get(cat, 0) + 1
        if cat == "indice menos selectivo":
            shapes = M01_LESS_SELECTIVE_SHAPES
        elif cat == "crecimiento de datos":
            shapes = M01_GROWTH_SHAPES
        else:
            shapes = M01_SHAPES
        shape = list(shapes)[n % len(shapes)]
        template = shapes[shape](t, c)[0]

        def run(cur, rng, template=template):
            _fetch(cur, template.format(**_m01_values(rng)))

        cases.append(Case(
            cid=f"{t}.{c}",
            role="control" if cat.startswith("control") else "positivo",
            category=cat,
            queries={"target": template.format(**_m01_values(random.Random(1)))},
            run=run,
            meta={"table": t, "column": c, "shape": shape},
        ))
    return cases


def m01_setup(engine):
    assignment = _m01_assignment(engine)
    cols = ", ".join(f"c{j} int NOT NULL" for j in range(M01_COLS))
    stmts = []
    if engine == "mysql":
        stmts.append("SET SESSION cte_max_recursion_depth = 1000000")
    stmts += ["DROP TABLE IF EXISTS ql_m01_dim",
              "CREATE TABLE ql_m01_dim (id int PRIMARY KEY, name varchar(20) NOT NULL)"]
    if engine == "postgres":
        stmts.append(f"INSERT INTO ql_m01_dim SELECT g, 'dim ' || g FROM generate_series(0, {M01_LO - 1}) g")
    else:
        stmts.append(f"""INSERT INTO ql_m01_dim WITH RECURSIVE g (n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM g WHERE n < {M01_LO - 1})
                         SELECT n, CONCAT('dim ', n) FROM g""")
    for i in range(M01_TABLES):
        t = _m01_t(i)
        stmts += [f"DROP TABLE IF EXISTS {t}",
                  f"CREATE TABLE {t} (id int PRIMARY KEY, {cols}, lo int NOT NULL, pad char(40) NOT NULL DEFAULT 'x')"]
        col_names = ", ".join(f"c{j}" for j in range(M01_COLS))
        if engine == "postgres":
            values = ", ".join(f"floor(random() * {M01_ROWS})::int" for _ in range(M01_COLS))
            stmts.append(f"INSERT INTO {t} (id, {col_names}, lo) SELECT g, {values}, floor(random() * {M01_LO})::int "
                         f"FROM generate_series(1, {M01_ROWS}) g")
        else:
            values = ", ".join(f"FLOOR(RAND() * {M01_ROWS})" for _ in range(M01_COLS))
            stmts.append(f"INSERT INTO {t} (id, {col_names}, lo) "
                         f"WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < {M01_ROWS}) "
                         f"SELECT n, {values}, FLOOR(RAND() * {M01_LO}) FROM g")
        has_less_selective = False
        for j in range(M01_COLS):
            cat = assignment[(i, j)]
            if cat == "indice menos selectivo":
                stmts.append(f"CREATE INDEX {t}_lo_c{j} ON {t} (lo, c{j})")
                has_less_selective = True
            else:
                stmts.append(f"CREATE INDEX {t}_c{j} ON {t} (c{j})")
        if has_less_selective:
            stmts.append(f"CREATE INDEX {t}_lo ON {t} (lo)")
        stmts.append(f"ANALYZE {t}" if engine == "postgres" else f"ANALYZE TABLE {t}")
    return stmts


def m01_inject(engine):
    """La degradacion de cada positivo. Primero lo rapido (indices), despues los inserts."""
    assignment = _m01_assignment(engine)
    fast, growth = [], []
    growth_cols = {}
    for (i, j), cat in sorted(assignment.items()):
        t = _m01_t(i)
        # Postgres: CONCURRENTLY, para no bloquear las lecturas de esa tabla mientras se borra
        if cat == "indice eliminado":
            fast.append(f"DROP INDEX CONCURRENTLY {t}_c{j}" if engine == "postgres" else f"DROP INDEX {t}_c{j} ON {t}")
        elif cat == "indice invisible":
            fast.append(f"ALTER TABLE {t} ALTER INDEX {t}_c{j} INVISIBLE")
        elif cat == "indice menos selectivo":
            fast.append(f"DROP INDEX CONCURRENTLY {t}_lo_c{j}" if engine == "postgres" else f"DROP INDEX {t}_lo_c{j} ON {t}")
        elif cat == "crecimiento de datos":
            growth_cols.setdefault(i, set()).add(j)
    for i, hot in sorted(growth_cols.items()):
        t = _m01_t(i)
        col_names = ", ".join(f"c{j}" for j in range(M01_COLS))
        if engine == "postgres":
            values = ", ".join(
                f"floor(random() * {M01_HOT if j in hot else M01_ROWS})::int" for j in range(M01_COLS))
            growth.append(f"INSERT INTO {t} (id, {col_names}, lo) SELECT 1000000 + g, {values}, "
                          f"floor(random() * {M01_LO})::int FROM generate_series(1, {M01_GROWTH_ROWS}) g")
        else:
            values = ", ".join(f"FLOOR(RAND() * {M01_HOT if j in hot else M01_ROWS})" for j in range(M01_COLS))
            growth.append(f"INSERT INTO {t} (id, {col_names}, lo) "
                          f"WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < {M01_GROWTH_ROWS}) "
                          f"SELECT 1000000 + n, {values}, FLOOR(RAND() * {M01_LO}) FROM g")
    pre = ["SET SESSION cte_max_recursion_depth = 1000000"] if engine == "mysql" and growth else []
    return fast + pre + growth


def m01_teardown(engine):
    return [f"DROP TABLE IF EXISTS {_m01_t(i)}" for i in range(M01_TABLES)] + ["DROP TABLE IF EXISTS ql_m01_dim"]


# =====================================================================
# AP-06 patron N+1
# =====================================================================

M06_PAIRS = 12              # 0..9 con positivos, 10 y 11 solo controles
M06_COMPOSITE = {8, 9}      # FK compuesta (a, b)
M06_PARENTS = 3000
M06_CHILDREN_PER_PARENT = 5
M06_CHILD_ROWS = M06_PARENTS * M06_CHILDREN_PER_PARENT
M06_R = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 75, 100]
# filas hijas por ronda que se busca generar en cada positivo (rondas de 5 s)
M06_CHILD_CALLS_PER_ROUND = 20


def _p(k):
    return f"ql_m06_p{k}"


def _c(k):
    return f"ql_m06_c{k}"


def m06_setup(engine):
    stmts = []
    if engine == "mysql":
        stmts.append("SET SESSION cte_max_recursion_depth = 1000000")
    for k in reversed(range(M06_PAIRS)):
        stmts.append(f"DROP TABLE IF EXISTS {_c(k)}")
        stmts.append(f"DROP TABLE IF EXISTS {_p(k)}")
    stmts += ["DROP TABLE IF EXISTS ql_m06_region",
              "CREATE TABLE ql_m06_region (id int PRIMARY KEY, label varchar(20) NOT NULL)"]
    if engine == "postgres":
        stmts.append("INSERT INTO ql_m06_region SELECT g, 'region ' || g FROM generate_series(0, 9) g")
    else:
        stmts.append("""INSERT INTO ql_m06_region WITH RECURSIVE g (n) AS (SELECT 0 UNION ALL SELECT n + 1 FROM g WHERE n < 9)
                        SELECT n, CONCAT('region ', n) FROM g""")
    num = "numeric(10,2)" if engine == "postgres" else "decimal(10,2)"
    for k in range(M06_PAIRS):
        p, c = _p(k), _c(k)
        if k in M06_COMPOSITE:
            stmts += [
                f"CREATE TABLE {p} (a int NOT NULL, b int NOT NULL, region int NOT NULL, flag int NOT NULL, "
                f"name varchar(40) NOT NULL, PRIMARY KEY (a, b))",
                f"CREATE TABLE {c} (id int PRIMARY KEY, pa int NOT NULL, pb int NOT NULL, amount {num} NOT NULL, "
                f"status int NOT NULL, note varchar(40) NOT NULL, "
                f"CONSTRAINT {c}_fk FOREIGN KEY (pa, pb) REFERENCES {p} (a, b))",
            ]
            if engine == "postgres":
                stmts += [
                    f"INSERT INTO {p} SELECT (g - 1) / 100 + 1, (g - 1) % 100 + 1, g % 10, g % 3, 'cliente ' || g "
                    f"FROM generate_series(1, {M06_PARENTS}) g",
                    f"INSERT INTO {c} SELECT g, ((g - 1) % {M06_PARENTS}) / 100 + 1, ((g - 1) % {M06_PARENTS}) % 100 + 1, "
                    f"g % 1000, g % 5, 'nota ' || g FROM generate_series(1, {M06_CHILD_ROWS}) g",
                    f"CREATE INDEX {c}_pa_pb ON {c} (pa, pb)",
                ]
            else:
                stmts += [
                    f"INSERT INTO {p} WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < {M06_PARENTS}) "
                    f"SELECT (n - 1) DIV 100 + 1, (n - 1) % 100 + 1, n % 10, n % 3, CONCAT('cliente ', n) FROM g",
                    f"INSERT INTO {c} WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < {M06_CHILD_ROWS}) "
                    f"SELECT n, ((n - 1) % {M06_PARENTS}) DIV 100 + 1, ((n - 1) % {M06_PARENTS}) % 100 + 1, "
                    f"n % 1000, n % 5, CONCAT('nota ', n) FROM g",
                ]
        else:
            stmts += [
                f"CREATE TABLE {p} (id int PRIMARY KEY, region int NOT NULL, flag int NOT NULL, name varchar(40) NOT NULL)",
                f"CREATE TABLE {c} (id int PRIMARY KEY, p_id int NOT NULL, amount {num} NOT NULL, status int NOT NULL, "
                f"note varchar(40) NOT NULL, CONSTRAINT {c}_fk FOREIGN KEY (p_id) REFERENCES {p} (id))",
            ]
            if engine == "postgres":
                stmts += [
                    f"INSERT INTO {p} SELECT g, g % 10, g % 3, 'cliente ' || g FROM generate_series(1, {M06_PARENTS}) g",
                    f"INSERT INTO {c} SELECT g, 1 + (g - 1) % {M06_PARENTS}, g % 1000, g % 5, 'nota ' || g "
                    f"FROM generate_series(1, {M06_CHILD_ROWS}) g",
                    f"CREATE INDEX {c}_p_id ON {c} (p_id)",
                ]
            else:
                stmts += [
                    f"INSERT INTO {p} WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < {M06_PARENTS}) "
                    f"SELECT n, n % 10, n % 3, CONCAT('cliente ', n) FROM g",
                    f"INSERT INTO {c} WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < {M06_CHILD_ROWS}) "
                    f"SELECT n, 1 + (n - 1) % {M06_PARENTS}, n % 1000, n % 5, CONCAT('nota ', n) FROM g",
                ]
        stmts += [f"ANALYZE {p}", f"ANALYZE {c}"] if engine == "postgres" else [f"ANALYZE TABLE {p}, {c}"]
    return stmts


def m06_teardown(engine):
    stmts = []
    for k in range(M06_PAIRS):
        stmts += [f"DROP TABLE IF EXISTS {_c(k)}", f"DROP TABLE IF EXISTS {_p(k)}"]
    return stmts + ["DROP TABLE IF EXISTS ql_m06_region"]


def _ids(start, count):
    return ", ".join(str(n) for n in range(start, start + count))


# Formas del padre: (SQL con {v} = primer id, {r} = filas). Devuelven r filas.
# uno_a_muchos, FK simple: el padre lee P y devuelve id
_PARENT_U = [
    "SELECT id, name FROM {P} WHERE id BETWEEN {v} AND {vr1}",
    "SELECT id, region FROM {P} WHERE id >= {v} ORDER BY id LIMIT {r}",
    "SELECT p.id, p.name FROM {P} p JOIN ql_m06_region g ON g.id = p.region WHERE p.id BETWEEN {v} AND {vr1}",
    "SELECT id, name, flag FROM {P} WHERE id > {v1} AND id < {vr}",
    "SELECT id FROM {P} WHERE id IN ({list})",
]
_CHILD_U = [
    "SELECT id, amount FROM {C} WHERE p_id = {x}",
    "SELECT * FROM {C} WHERE p_id = {x}",
    "SELECT COUNT(*) FROM {C} WHERE p_id = {x}",
    "SELECT id, status FROM {C} WHERE p_id = {x} AND status = {st}",
    "SELECT id, amount, note FROM {C} WHERE p_id = {x} ORDER BY id DESC LIMIT 3",
]
# muchos_a_uno, FK simple: el padre lee C y devuelve p_id
_PARENT_M = [
    "SELECT id, p_id FROM {C} WHERE id BETWEEN {v} AND {vr1}",
    "SELECT id, p_id, amount FROM {C} WHERE id >= {v} ORDER BY id LIMIT {r}",
    "SELECT c.id, c.p_id FROM {C} c JOIN ql_m06_region g ON g.id = c.status WHERE c.id BETWEEN {v} AND {vr1}",
    "SELECT id, p_id, note FROM {C} WHERE id > {v1} AND id < {vr}",
    "SELECT id, p_id FROM {C} WHERE id IN ({list})",
]
_CHILD_M = [
    "SELECT name FROM {P} WHERE id = {x}",
    "SELECT * FROM {P} WHERE id = {x}",
    "SELECT id, region FROM {P} WHERE id = {x}",
    "SELECT name, flag FROM {P} WHERE id = {x} AND flag >= 0",
    "SELECT p.name, p.region FROM {P} p WHERE p.id = {x} LIMIT 1",
]
# FK compuesta (a, b): el padre devuelve (a, b) / (pa, pb), r <= 100 dentro de un mismo a
_PARENT_U_COMP = [
    "SELECT a, b, name FROM {P} WHERE a = {a} AND b BETWEEN {v} AND {vr1}",
    "SELECT a, b, region FROM {P} WHERE a = {a} AND b >= {v} ORDER BY b LIMIT {r}",
    "SELECT p.a, p.b FROM {P} p JOIN ql_m06_region g ON g.id = p.region WHERE p.a = {a} AND p.b BETWEEN {v} AND {vr1}",
    "SELECT a, b, flag FROM {P} WHERE a = {a} AND b > {v1} AND b < {vr}",
    "SELECT a, b FROM {P} WHERE a = {a} AND b IN ({list})",
]
_CHILD_U_COMP = [
    "SELECT id, amount FROM {C} WHERE pa = {xa} AND pb = {xb}",
    "SELECT * FROM {C} WHERE pa = {xa} AND pb = {xb}",
    "SELECT COUNT(*) FROM {C} WHERE pa = {xa} AND pb = {xb}",
    "SELECT id, status FROM {C} WHERE pa = {xa} AND pb = {xb} AND status = {st}",
    "SELECT id, amount, note FROM {C} WHERE pa = {xa} AND pb = {xb} ORDER BY id DESC LIMIT 3",
]
_PARENT_M_COMP = [
    "SELECT id, pa, pb FROM {C} WHERE id BETWEEN {v} AND {vr1}",
    "SELECT id, pa, pb, amount FROM {C} WHERE id >= {v} ORDER BY id LIMIT {r}",
    "SELECT c.id, c.pa, c.pb FROM {C} c JOIN ql_m06_region g ON g.id = c.status WHERE c.id BETWEEN {v} AND {vr1}",
    "SELECT id, pa, pb, note FROM {C} WHERE id > {v1} AND id < {vr}",
    "SELECT id, pa, pb FROM {C} WHERE id IN ({list})",
]
_CHILD_M_COMP = [
    "SELECT name FROM {P} WHERE a = {xa} AND b = {xb}",
    "SELECT * FROM {P} WHERE a = {xa} AND b = {xb}",
    "SELECT region, flag FROM {P} WHERE a = {xa} AND b = {xb}",
    "SELECT name, flag FROM {P} WHERE a = {xa} AND b = {xb} AND flag >= 0",
    "SELECT p.name, p.region FROM {P} p WHERE p.a = {xa} AND p.b = {xb} LIMIT 1",
]


def _parent_values(rng, r, max_id, composite):
    if composite:
        v = rng.randrange(1, 100 - r + 2)
        a = rng.randrange(1, M06_PARENTS // 100 + 1)
    else:
        v = rng.randrange(1, max_id - r + 2)
        a = None
    return {"v": v, "vr1": v + r - 1, "v1": v - 1, "vr": v + r, "r": r, "a": a, "list": _ids(v, r)}


def _m06_positive(engine, k, direction, shape, r):
    composite = k in M06_COMPOSITE
    P, C = _p(k), _c(k)
    if direction == "uno_a_muchos":
        parent_t = (_PARENT_U_COMP if composite else _PARENT_U)[shape]
        child_t = (_CHILD_U_COMP if composite else _CHILD_U)[shape]
        max_id = M06_PARENTS
    else:
        parent_t = (_PARENT_M_COMP if composite else _PARENT_M)[shape]
        child_t = (_CHILD_M_COMP if composite else _CHILD_M)[shape]
        max_id = M06_CHILD_ROWS
    if composite and direction == "uno_a_muchos":
        r = min(r, 100)

    def keys(row):
        # (x) o (xa, xb) desde la fila del padre
        if composite:
            return {"xa": row[-2] if direction == "uno_a_muchos" else row[1],
                    "xb": row[-1] if direction == "uno_a_muchos" else row[2]}
        return {"x": row[0] if direction == "uno_a_muchos" else row[1]}

    def parent_sql(rng):
        return parent_t.format(P=P, C=C, **_parent_values(rng, r, max_id, composite and direction == "uno_a_muchos"))

    def child_sql(rng, key):
        return child_t.format(P=P, C=C, st=rng.randrange(5), **key)

    def run(cur, rng):
        rows = _fetch(cur, parent_sql(rng))
        for row in rows:
            # uno_a_muchos compuesto: (a, b, ...) -> a y b son las dos primeras columnas
            key = keys(row) if not (composite and direction == "uno_a_muchos") else {"xa": row[0], "xb": row[1]}
            _fetch(cur, child_sql(rng, key))

    sample_rng = random.Random(3)
    sample_key = {"xa": 1, "xb": 1} if composite else {"x": 1}
    return Case(
        cid=f"p{k}-{'u' if direction == 'uno_a_muchos' else 'm'}{shape}-r{r}",
        role="positivo",
        category=f"{direction}{' (FK compuesta)' if composite else ''}",
        queries={"target": child_sql(sample_rng, sample_key), "parent": parent_sql(sample_rng)},
        run=run,
        per_round=max(1, -(-M06_CHILD_CALLS_PER_ROUND // r)),
        meta={"pair": k, "direction": direction, "shape": shape, "r": r, "composite": composite},
    )


def _m06_dedupe(k, r):
    """N+1 real con cache: el padre trae cada p_id dos veces y la aplicacion
    consulta cada id una sola vez. La hija corre r veces para 2r filas: queda
    fuera del +-20 % de la regla."""
    P, C = _p(k), _c(k)
    parent_t = "SELECT id, p_id FROM {C} WHERE id BETWEEN {v} AND {vr1} OR id BETWEEN {w} AND {wr1}"
    child_t = "SELECT region, name FROM {P} WHERE id = {x}"

    def parent_sql(rng):
        v = rng.randrange(1, M06_PARENTS - r + 2)
        return parent_t.format(C=C, v=v, vr1=v + r - 1, w=v + M06_PARENTS, wr1=v + M06_PARENTS + r - 1)

    def run(cur, rng):
        seen = set()
        for row in _fetch(cur, parent_sql(rng)):
            if row[1] in seen:
                continue
            seen.add(row[1])
            _fetch(cur, child_t.format(P=P, x=row[1]))

    return Case(
        cid=f"p{k}-cache-r{r}",
        role="extra",
        category="muchos_a_uno con cache (fuera de la regla)",
        queries={"target": child_t.format(P=P, x=1), "parent": parent_sql(random.Random(3))},
        run=run,
        per_round=max(1, -(-M06_CHILD_CALLS_PER_ROUND // r)),
        meta={"pair": k, "r": r},
    )


# columnas de los controles, distintas de las de los positivos para que cada
# consulta tenga su propio query_id
_CTRL_C_COLS = ["amount, id", "note, id", "status, amount", "id, note, status", "amount, status, id"]
_CTRL_P_COLS = ["region, id", "flag, name", "name, region, flag", "id, flag", "region, name, id"]
# hijas de las paginas de detalle
_CTRL_D_COLS = ["status, id", "id, amount, status", "note, amount", "amount, note, id", "status, note"]


def _m06_controls():
    cases = []
    # (pares, grupo): compartidos con positivos y pares solo de control
    plan = [((0, 1, 2, 3, 4, 5, 6, 7), "pares con N+1"), ((10, 11), "pares sin N+1")]
    for pairs, group in plan:
        n_each = 10 if group == "pares con N+1" else 5
        # consultas sueltas por la FK, sin padre (ritmo fijo por caso)
        for n in range(n_each):
            k = pairs[n % len(pairs)]
            cols = _CTRL_C_COLS[n % len(_CTRL_C_COLS)]
            extra = f" AND status >= {n // len(_CTRL_C_COLS)}" if n >= len(_CTRL_C_COLS) else ""
            sql_t = f"SELECT {cols} FROM {_c(k)} WHERE p_id = {{x}}{extra}"
            calls = 8 + (n * 17) % 73   # 8..80 por ronda de 5 s

            def run(cur, rng, sql_t=sql_t, calls=calls):
                for _ in range(calls):
                    _fetch(cur, sql_t.format(x=rng.randrange(1, M06_PARENTS + 1)))

            cases.append(Case(
                cid=f"ctl-suelta-p{k}-{n}", role="control", category=f"consulta suelta por FK ({group})",
                queries={"target": sql_t.format(x=1)}, run=run, meta={"pair": k, "calls_per_round": calls},
            ))
        # pagina de detalle 1 + 1: el padre devuelve una fila
        for n in range(n_each):
            k = pairs[(n + 3) % len(pairs)]
            pcols = _CTRL_P_COLS[n % len(_CTRL_P_COLS)]
            ccols = _CTRL_D_COLS[n % len(_CTRL_D_COLS)]
            extra = f" AND amount >= {n // len(_CTRL_C_COLS)}" if n >= len(_CTRL_C_COLS) else ""
            parent_t = f"SELECT {pcols} FROM {_p(k)} WHERE id = {{x}}{extra.replace('amount', 'flag')}"
            child_t = f"SELECT {ccols} FROM {_c(k)} WHERE p_id = {{x}}{extra}"

            def run(cur, rng, parent_t=parent_t, child_t=child_t):
                x = rng.randrange(1, M06_PARENTS + 1)
                _fetch(cur, parent_t.format(x=x))
                _fetch(cur, child_t.format(x=x))

            cases.append(Case(
                cid=f"ctl-detalle-p{k}-{n}", role="control", category=f"detalle 1 + 1 ({group})",
                queries={"target": child_t.format(x=1), "parent": parent_t.format(x=1)}, run=run,
                per_round=10, meta={"pair": k},
            ))
    # solo en los pares sin N+1: JOIN e IN (...)
    for n in range(5):
        k = (10, 11)[n % 2]
        r = (5, 10, 20, 40, 80)[n]
        cols = ("c.id, c.amount", "c.id, c.note", "p.name, c.id", "c.status, p.id", "c.amount, p.region")[n]
        join_t = (f"SELECT {cols} FROM {_p(k)} p JOIN {_c(k)} c ON c.p_id = p.id "
                  f"WHERE p.id BETWEEN {{v}} AND {{vr1}}")

        def run(cur, rng, join_t=join_t, r=r):
            v = rng.randrange(1, M06_PARENTS - r + 2)
            _fetch(cur, join_t.format(v=v, vr1=v + r - 1))

        cases.append(Case(
            cid=f"ctl-join-p{k}-{n}", role="control", category="JOIN en una consulta (pares sin N+1)",
            queries={"target": join_t.format(v=1, vr1=r)}, run=run, per_round=2, meta={"pair": k, "r": r},
        ))
    for n in range(5):
        k = (11, 10)[n % 2]
        r = (5, 10, 20, 40, 80)[n]
        pcols = ("id", "id, name", "id, region", "id, flag", "id, name, region")[n]
        parent_t = f"SELECT {pcols} FROM {_p(k)} WHERE id BETWEEN {{v}} AND {{vr1}}"
        child_t = f"SELECT {_CTRL_C_COLS[n]}, p_id FROM {_c(k)} WHERE p_id IN ({{ids}})"

        def run(cur, rng, parent_t=parent_t, child_t=child_t, r=r):
            v = rng.randrange(1, M06_PARENTS - r + 2)
            rows = _fetch(cur, parent_t.format(v=v, vr1=v + r - 1))
            if rows:
                _fetch(cur, child_t.format(ids=", ".join(str(row[0]) for row in rows)))

        cases.append(Case(
            cid=f"ctl-in-p{k}-{n}", role="control", category="IN (...) en lote (pares sin N+1)",
            queries={"target": child_t.format(ids=_ids(1, r)), "parent": parent_t.format(v=1, vr1=r)},
            run=run, per_round=2, meta={"pair": k, "r": r},
        ))
    return cases


def m06_cases(engine):
    cases = []
    n = 0
    for k in range(10):
        for direction in ("uno_a_muchos", "muchos_a_uno"):
            for shape in range(5):
                cases.append(_m06_positive(engine, k, direction, shape, M06_R[n % len(M06_R)]))
                n += 1
    for k in range(10):
        if k not in M06_COMPOSITE:
            cases.append(_m06_dedupe(k, (4, 6, 10, 20, 40, 8, 15, 30)[k % 8]))
    return cases + _m06_controls()


# =====================================================================

MEASUREMENTS = {
    "ap07": {"antipatron": "disk_spill", "setup": m07_setup, "teardown": m07_teardown, "pool": m07_pool},
    "ap01": {"antipatron": "baseline_degradation", "setup": m01_setup, "teardown": m01_teardown,
             "cases": m01_cases, "inject": m01_inject},
    "ap06": {"antipatron": "n_plus_one", "setup": m06_setup, "teardown": m06_teardown, "cases": m06_cases},
}
