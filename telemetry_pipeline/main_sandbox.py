"""Entrada del sandbox: el par fijo postgres/mysql.

En produccion el pipeline sale de `main.py`, que lee las bases registradas en
`registered_databases`. Este script conserva el comportamiento anterior para
`ql_sandbox`, para la capa de integracion y para `measure_overhead`, donde no
hay filas registradas: dos targets fijos resueltos por entorno con
`MONITOR_PG_*` / `MONITOR_MY_*`.

    python main_sandbox.py
"""
from config.connections import get_connection_mysql, get_connection_postgres
from config.registered import Target
from main import run_targets

ENGINES = (
    ("postgres", get_connection_postgres),
    ("mysql", get_connection_mysql),
)


def main():
    run_targets([Target(dialect, factory) for dialect, factory in ENGINES])


if __name__ == "__main__":
    main()
