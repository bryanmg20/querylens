import re

import pytest

from collectors.mysql.queries import STATEMENTS_QUERY
from collectors.postgres.queries import STATEMENTS_QUERY as PG_STATEMENTS_QUERY

pytestmark = pytest.mark.contract


def test_stmt_query_defines_avg_rows_per_call():
    assert "AS avg_rows_per_call" in STATEMENTS_QUERY


def test_stmt_query_keeps_fractional_avg_rows():
    assert re.search(
        r"ROUND\(s\.SUM_ROWS_SENT / NULLIF\(s\.COUNT_STAR, 0\), [1-9]\)",
        STATEMENTS_QUERY,
    )


def test_pg_stmt_query_keeps_fractional_avg_rows():
    assert re.search(
        r"ROUND\(rows::numeric / NULLIF\(calls, 0\), [1-9]\)",
        PG_STATEMENTS_QUERY,
    )


def test_stmt_query_exposes_stddev_and_coeff():
    """MySQL no calcula la desviacion: performance_schema no la da. El contrato
    exige que la columna exista, pero como NULL, no un agregado cualquiera.

    Aceptar 'NULL AS stddev_time_ms' era lo que hacia el assert vacuamente
    cierto: la query ya traia NULL y el test pasaba sin exigir nada.
    """
    assert re.search(
        r"NULL\s+AS stddev_time_ms", STATEMENTS_QUERY
    ), "MySQL debe declarar stddev_time_ms como NULL, no calcularlo a mano"
    assert re.search(
        r"NULL\s+AS coeff_of_variation", STATEMENTS_QUERY
    ), "MySQL no puede derivar el coeficiente de variacion de MIN/MAX/AVG"
    # El calculo real vive en el collector, no en la query.
    from collectors.mysql.collector import Mysql_Collector

    assert hasattr(Mysql_Collector, "calculate_stddev_coeff")


def test_mysql_autofiltrado_cubre_toda_la_huella_del_pipeline():
    """El filtro SQL y PIPELINE_FINGERPRINT tienen que decir lo mismo.

    Son dos listas que cumplen la misma funcion en lugares distintos: una
    excluye en la query, la otra mide la contaminacion en los goldens. Cuando
    divergieron, COMMIT aparecio en statements y el test de integracion lo
    cazo. Este test es el que evita que vuelvan a separarse en silencio.
    """
    from ci.regenerate_goldens import PIPELINE_FINGERPRINT

    # Los backticks se normalizan en los dos lados: el digest trae
    # 'SET `AUTOCOMMIT`' y tanto el filtro como la huella pueden escribirse con o
    # sin ellos.
    normalizado = STATEMENTS_QUERY.upper().replace("`", "")
    for huella in PIPELINE_FINGERPRINT:
        # Sin strip: el espacio final de 'use ' es parte del prefijo real
        # ('USE %'), no ruido. Strippearlo buscaria 'USE%' y no encontraria nada.
        prefijo = huella.upper().replace("`", "")
        cubierto = f"NOT LIKE '{prefijo}%" in normalizado
        assert cubierto, (
            f"la huella {huella!r} no tiene filtro en STATEMENTS_QUERY: "
            "ampliar el filtro o el fingerprint, pero no dejarlos distintos"
        )