import pytest

from stages.canonicalizers import (
    MAX_STATEMENT_CANONIC_CHARS,
    canonicalize_query,
    clean_mysql_sintax,
    create_canonic_queries,
    normalize_querytext_active,
    separate_pg_params,
)

pytestmark = pytest.mark.unit


def test_canonicalize_query_replaces_literals():
    q = canonicalize_query("SELECT * FROM t WHERE id = 42", "postgres")
    assert "42" not in q
    assert "$1" in q


def test_canonicalize_query_empty_returns_none():
    assert canonicalize_query("", "postgres") is None
    assert canonicalize_query(None, "postgres") is None


def test_canonicalize_query_fallback_on_parse_error():
    text = "  SELECT %s WHERE ---  "
    q = canonicalize_query(text, "postgres")
    assert q == "Not available"


def test_canonicalize_query_mysql_dialect():
    q = canonicalize_query("SELECT a FROM t WHERE b LIKE 'x%'", "mysql")
    assert q == "SELECT a FROM t WHERE b LIKE $1"


def test_clean_mysql_sintax():
    assert clean_mysql_sintax("SELECT DISTINCTROW a FROM t") == "SELECT DISTINCT a FROM t"


def test_create_canonic_queries_pure():
    stats = {
        "top_impact_queries": [
            {"query_id": 1, "query_text": "SELECT 1"}
        ]
    }
    create_canonic_queries(stats)
    assert stats["top_impact_queries"][0]["canonic_query"] == "SELECT $1"


def test_create_canonic_queries_clean_mysql():
    stats = {
        "top_impact_queries": [
            {"query_id": 1, "query_text": "SELECT DISTINCTROW 1"}
        ]
    }
    create_canonic_queries(stats, source_dialect="mysql", clean_mysql=True)
    assert "DISTINCTROW" not in stats["top_impact_queries"][0]["canonic_query"]


def test_normalize_querytext_active_drops_key():
    stats = {"active_queries": [{"query_id": 1, "query_text": "SELECT 1"}]}
    normalize_querytext_active(stats)
    row = stats["active_queries"][0]
    assert "query_text" not in row
    assert "canonic_query" in row


def test_normalize_querytext_active_empty():
    stats = {"active_queries": [{"query_id": 1, "query_text": None}]}
    normalize_querytext_active(stats)
    assert stats["active_queries"][0]["canonic_query"] == "Not available"


def test_normalize_querytext_active_none_section():
    """N-1: CollectStage deja active_queries en None cuando falla; el loop no
    debe crashear con TypeError por iterar sobre None."""
    stats = {"active_queries": None}
    normalize_querytext_active(stats)
    assert stats["active_queries"] is None

@pytest.mark.parametrize(
    "query_text, dialect, secret",
    [
        ("SELECT * FROM u WHERE token = 0xDEADBEEF", "mysql", "DEADBEEF"),
        ("SELECT * FROM u WHERE id = X'0A1B'", "mysql", "0A1B"),
        ("SELECT * FROM u WHERE f = b'1010'", "mysql", "1010"),
        ("SELECT * FROM u WHERE id = X'1F2E'", "postgres", "1F2E"),
        ("SELECT * FROM u WHERE f = B'0110'", "postgres", "0110"),
    ],
)
def test_canonicalize_query_redacts_hex_and_bit_literals(query_text, dialect, secret):
    """HexString/BitString no son exp.Literal en sqlglot: sin cubrirlos, el
    valor (UUIDs/hashes de columnas BINARY) viajaba crudo en canonic_query de
    active_queries."""
    q = canonicalize_query(query_text, dialect)
    assert secret not in q.upper()
    assert "$1" in q


@pytest.mark.parametrize(
    "query_text, expected",
    [
        # Texto real de pg_stat_statements para IN (1,2,3) sin espacios.
        (
            "SELECT a, pg_sleep($1) FROM probe_in_list WHERE x IN ($2,$3,$4)",
            "SELECT a, PG_SLEEP($1) FROM probe_in_list WHERE x IN ($2, $3, $4)",
        ),
        ("INSERT INTO t VALUES ($1,$2)", "INSERT INTO t VALUES ($1, $2)"),
        ("SELECT f($1,$2)", "SELECT F($1, $2)"),
        ("SELECT $1+$2", "SELECT $1 + $2"),
        ("SELECT $1||$2", "SELECT $1 || $2"),
        ("SELECT ($1),$2", "SELECT ($1), $2"),
        ("SELECT $10,$11", "SELECT $1, $2"),
    ],
)
def test_canonicalize_query_pg_params_without_spaces(query_text, expected):
    assert canonicalize_query(query_text, "postgres") == expected


def test_separate_pg_params_keeps_identifiers_with_dollar():
    assert separate_pg_params("SELECT col$1,$2 FROM t$9") == "SELECT col$1,$2 FROM t$9"
    assert canonicalize_query("SELECT col$1 FROM t WHERE x = $1", "postgres") == (
        "SELECT col$1 FROM t WHERE x = $1"
    )


def test_canonicalize_query_mysql_does_not_separate_dollar():
    assert canonicalize_query("SELECT `col$1x` FROM t", "mysql") == "SELECT col$1x FROM t"


def test_create_canonic_queries_covers_statements():
    stats = {
        "statements": [
            {"query_id": 1, "query_text": "SELECT a FROM t WHERE x = $1"},
            {"query_id": 2, "query_text": "SELECT b FROM u WHERE y IN ($1,$2)"},
            {"query_id": 3, "query_text": ""},
        ],
        "top_impact_queries": [{"query_id": 1, "query_text": "SELECT a FROM t WHERE x = $1"}],
    }
    create_canonic_queries(stats)
    canonic = [s["canonic_query"] for s in stats["statements"]]
    assert canonic == [
        "SELECT a FROM t WHERE x = $1",
        "SELECT b FROM u WHERE y IN ($1, $2)",
        None,
    ]
    assert stats["top_impact_queries"][0]["canonic_query"] == canonic[0]


def test_create_canonic_queries_parses_each_text_once(monkeypatch):
    """Los candidatos son copias de statements y en MySQL el mismo digest se
    repite entre schemas: cada texto distinto se canonicaliza una sola vez."""
    import stages.canonicalizers as canonicalizers

    calls = []
    real = canonicalizers.canonicalize_query
    monkeypatch.setattr(
        canonicalizers,
        "canonicalize_query",
        lambda text, dialect: calls.append(text) or real(text, dialect),
    )
    text = "SELECT DISTINCTROW `k` FROM `t` WHERE `id` = ?"
    stats = {
        "statements": [
            {"query_id": "a/d1", "query_text": text},
            {"query_id": "b/d1", "query_text": text},
        ],
        "top_impact_queries": [{"query_id": "a/d1", "query_text": text}],
    }
    canonicalizers.create_canonic_queries(stats, "mysql", clean_mysql=True)
    assert len(calls) == 1
    rows = stats["statements"] + stats["top_impact_queries"]
    assert {r["canonic_query"] for r in rows} == {"SELECT DISTINCT k FROM t WHERE id = $1"}


def _long_insert():
    rows = ", ".join(f"(${i}, ${i + 1})" for i in range(1, 2000, 2))
    text = f"INSERT INTO t (a, b) VALUES {rows}"
    assert len(text) > MAX_STATEMENT_CANONIC_CHARS
    return text


def test_create_canonic_queries_skips_long_statements():
    stats = {"statements": [{"query_id": 1, "query_text": _long_insert()}]}
    create_canonic_queries(stats)
    assert stats["statements"][0]["canonic_query"] == "Not available"


def test_create_canonic_queries_long_candidate_is_not_capped():
    """El tope es del volumen de statements, no de los candidatos: un candidato
    largo se canonicaliza y su statement reutiliza ese resultado."""
    text = _long_insert()
    stats = {
        "statements": [{"query_id": 1, "query_text": text}],
        "top_impact_queries": [{"query_id": 1, "query_text": text}],
    }
    create_canonic_queries(stats)
    canonic = stats["top_impact_queries"][0]["canonic_query"]
    assert canonic.startswith("INSERT INTO t (a, b) VALUES ($1, $2), ($3, $4)")
    assert stats["statements"][0]["canonic_query"] == canonic
