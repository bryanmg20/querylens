import re

import pytest

from collectors.mysql.queries import STATEMENTS_QUERY

pytestmark = pytest.mark.contract


def test_stmt_query_defines_avg_rows_per_call():
    assert "AS avg_rows_per_call" in STATEMENTS_QUERY


def test_stmt_query_keeps_fractional_avg_rows():
    assert re.search(
        r"ROUND\(s\.SUM_ROWS_SENT / NULLIF\(s\.COUNT_STAR, 0\), [1-9]\)",
        STATEMENTS_QUERY,
    )


def test_stmt_query_exposes_stddev_and_coeff():
    assert "coeff_of_variation" in STATEMENTS_QUERY
    assert "stddev_time_ms" in STATEMENTS_QUERY or "STDDEV" in STATEMENTS_QUERY