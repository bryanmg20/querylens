"""GET /diagnostics contra Postgres, con las tablas reales de querylens_database/."""

import json
from datetime import datetime, timedelta

import pytest

from app.routers.diagnostics import CATALOG, MAX_FINDINGS
from conftest import make_token

pytestmark = pytest.mark.db

DB = "db_test0001"
OTRA = "db_test0002"


class Seed:
    """Arma filas como las dejan el auth service, el detector y el pipeline.
    Los tiempos van relativos a now() del servidor: `hace` es un timedelta."""

    def __init__(self, conn):
        self.conn = conn

    def _one(self, sql, params):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0] if cur.description else None

    def base(self, ident=DB, *, engine="postgresql", sesion_hace=timedelta(hours=1), monitoreo=True):
        self._one(
            "INSERT INTO registered_databases (database_identifier, connection_fingerprint, connection_name, engine, "
            "  host, port, db_user, encrypted_password, database_name, monitoring_enabled, detection_session_started_at) "
            "VALUES (%s, %s, %s, %s, 'h', 'p', 'u', 'x', 'ventas', %s, now() - %s)",
            (ident, ident.ljust(64, "0"), f"conexion {ident}", engine, monitoreo, sesion_hace),
        )

    def problema(self, *, db=DB, antipatron="missing_index", codigo="AP-04", severidad="medium", clave=None,
                 query_id="q1", tabla="pedidos", base="ventas", riesgo="Bajo", impacto_ms=None, evidencia=None):
        return self._one(
            "INSERT INTO public.problemas (db_id, antipatron, finding_key, severidad, query_id, table_name, "
            "  database_name, evidencia, explicacion, recomendacion, codigo, riesgo, impacto_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'explicacion', 'recomendacion', %s, %s, %s) RETURNING id",
            (db, antipatron, clave or f"{antipatron}:{query_id}:{tabla}", severidad, query_id, tabla, base,
             json.dumps(evidencia or {"live_rows": 1000}), codigo, riesgo, impacto_ms),
        )

    def deteccion(self, problema_id, *, hace=timedelta(minutes=5), severidad="medium", impacto_ms=None):
        self._one(
            "INSERT INTO public.detecciones (problema_id, severidad, detectado_en, impacto_ms) VALUES (%s, %s, now() - %s, %s)",
            (problema_id, severidad, hace, impacto_ms),
        )

    def estado(self, codigo, antipatron, estado, *, db=DB, hasta_hace=timedelta(minutes=1), ventanas=1, motivos=None):
        self._one(
            "INSERT INTO public.estado_evaluacion (db_id, codigo, antipatron, tramo, estado, motivos, detalle, ventanas, desde, hasta) "
            "VALUES (%s, %s, %s, date_trunc('minute', now() - %s), %s, %s, '{}', %s, now() - %s - interval '10 seconds', now() - %s)",
            (db, codigo, antipatron, hasta_hace, estado, json.dumps(motivos or []), ventanas, hasta_hace, hasta_hace),
        )

    def last_run(self, status, *, db=DB, hace=timedelta(seconds=10)):
        self._one(
            "INSERT INTO pipeline_health.last_run (db_id, started_at, finished_at, status, sections_ok, sections_failed) "
            "VALUES (%s, now() - %s - interval '1 second', now() - %s, %s, '{statements}', '{}')",
            (db, hace, hace, status),
        )

    def muestra(self, *, db=DB, hace=timedelta(minutes=1), deltas=0, reset=False, desborde=False, segundos=10.0):
        self._one(
            "INSERT INTO pipeline_health.cycle_samples (db_id, taken_at, deadlocks_since_prev, counter_reset, "
            "  log_window_overrun, seconds_since_prev) VALUES (%s, now() - %s, %s, %s, %s, %s)",
            (db, hace, deltas, reset, desborde, segundos),
        )

    def abrazo(self, clave, *, db=DB, hace=timedelta(minutes=1), completo=True, antipatron="inconsistent_lock_order_deadlock"):
        self._one(
            "INSERT INTO public.deadlock_events (db_id, report_key, detected_at, source, complete, antipatron) "
            "VALUES (%s, %s, now() - %s, 'postgres_log', %s, %s)",
            (db, clave, hace, completo, antipatron if completo else None),
        )

    def issue(self, code, severity, *, db=DB, resuelto_hace=None, visto_hace=timedelta(minutes=1)):
        self._one(
            "INSERT INTO pipeline_health.issues (db_id, code, scope, category, severity, section, message, remediation, "
            "  last_seen, resolved_at) "
            "VALUES (%s, %s, 'db', 'permisos', %s, 'statements', %s, 'haz esto', now() - %s, now() - %s::interval)",
            (db, code, severity, f"mensaje de {code}", visto_hace, resuelto_hace),
        )


@pytest.fixture
def seed(db_conn):
    return Seed(db_conn)


def get(client, ident=DB, **params):
    return client.get("/diagnostics", params=params, headers={"Authorization": f"Bearer {make_token(ident)}"})


# --- acceso ----------------------------------------------------------------


def test_sin_token_responde_401(client):
    assert client.get("/diagnostics").status_code == 401


@pytest.mark.parametrize(
    "token",
    [make_token(key="otra-clave"), make_token(expires_in=timedelta(seconds=-1)), make_token(sub=None)],
    ids=["otra-clave", "vencido", "sin-sub"],
)
def test_token_invalido_responde_401(client, token):
    r = client.get("/diagnostics", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_base_no_registrada_responde_404(client):
    r = get(client, "db_noexiste")
    assert r.status_code == 404
    assert "ya no está registrada" in r.json()["detail"]


@pytest.mark.parametrize("since_hours", [0, 24 * 15 + 1])
def test_since_hours_fuera_de_rango_responde_422(client, seed, since_hours):
    seed.base()
    assert get(client, since_hours=since_hours).status_code == 422


def test_health_de_la_api(client):
    assert client.get("/health").json() == {"status": "ok"}


# --- base sin datos -------------------------------------------------------------


def test_base_sin_datos(client, seed):
    seed.base(engine="mysql", monitoreo=False)
    r = get(client)
    assert r.status_code == 200
    body = r.json()
    assert body["database_identifier"] == DB
    assert body["connection_name"] == f"conexion {DB}"
    assert body["engine"] == "mysql"
    assert body["is_active"] is True
    assert body["monitoring_enabled"] is False
    assert body["diagnostics"] == []
    assert body["evaluation"] == []
    assert body["activity"] == []
    assert body["health"] == {"last_run": None, "issues": []}
    assert body["server_time"]


def test_tabla_que_todavia_no_existe_no_da_500(client, seed, db_conn):
    # Volumen viejo, o el detector todavia no creo la tabla: la seccion viene vacia.
    seed.base()
    with db_conn.cursor() as cur:
        cur.execute("ALTER TABLE public.detecciones RENAME TO detecciones_aparte")
    try:
        r = get(client)
        assert r.status_code == 200
        assert r.json()["diagnostics"] == []
        assert r.json()["activity"] == []
    finally:
        with db_conn.cursor() as cur:
            cur.execute("ALTER TABLE public.detecciones_aparte RENAME TO detecciones")


# --- hallazgos: un problema por fila ----------------------------------------------


def test_un_problema_por_fila_con_lo_de_la_sesion(client, seed):
    seed.base(sesion_hace=timedelta(hours=1))
    p = seed.problema(severidad="low", base="ventas", impacto_ms=7.0, riesgo="Bloquea escrituras")
    seed.deteccion(p, hace=timedelta(hours=3), severidad="high", impacto_ms=1000)  # antes de la sesion
    seed.deteccion(p, hace=timedelta(minutes=30), severidad="high", impacto_ms=10)
    seed.deteccion(p, hace=timedelta(minutes=20), severidad="low", impacto_ms=5)
    seed.deteccion(p, hace=timedelta(minutes=10), severidad="low", impacto_ms=7)

    body = get(client).json()
    findings = body["diagnostics"]
    assert len(findings) == 1
    f = findings[0]
    assert f["id"] == p
    assert f["antipatron"] == "missing_index"
    assert f["codigo"] == "AP-04"
    assert f["database_name"] == "ventas"
    assert f["table_name"] == "pedidos"
    assert f["riesgo"] == "Bloquea escrituras"
    # La severidad mas alta de la sesion; la actual es la del problema (ultima deteccion).
    assert f["severidad"] == "high"
    assert f["severidad_actual"] == "low"
    assert f["veces_sesion"] == 3
    assert f["veces_total"] == 4
    assert f["por_severidad"] == {"high": 1, "low": 2}
    # Solo el impacto de la sesion, no el de la deteccion anterior.
    assert f["impacto_sesion_ms"] == pytest.approx(22)
    assert f["impacto_ms"] == pytest.approx(7)
    assert f["evidencia"] == {"live_rows": 1000}
    assert f["explicacion"] == "explicacion"
    assert f["recomendacion"] == "recomendacion"
    # detectado_en es la ultima deteccion de la sesion (hace 10 min).
    hace = datetime.fromisoformat(body["server_time"]) - datetime.fromisoformat(f["detectado_en"])
    assert timedelta(minutes=9) < hace < timedelta(minutes=11)


def test_problema_sin_detecciones_en_la_sesion_no_sale(client, seed):
    seed.base(sesion_hace=timedelta(minutes=30))
    viejo = seed.problema(query_id="viejo")
    seed.deteccion(viejo, hace=timedelta(hours=2))
    nuevo = seed.problema(query_id="nuevo")
    seed.deteccion(nuevo, hace=timedelta(minutes=5))
    assert [f["id"] for f in get(client).json()["diagnostics"]] == [nuevo]


def test_solo_los_problemas_de_la_base_del_token(client, seed):
    seed.base(DB)
    seed.base(OTRA)
    propio = seed.problema(db=DB, query_id="a")
    ajeno = seed.problema(db=OTRA, query_id="b")
    seed.deteccion(propio)
    seed.deteccion(ajeno)
    assert [f["id"] for f in get(client, DB).json()["diagnostics"]] == [propio]
    assert [f["id"] for f in get(client, OTRA).json()["diagnostics"]] == [ajeno]


def test_orden_por_impacto_de_la_sesion_y_desempate_por_severidad(client, seed):
    seed.base()
    chico = seed.problema(query_id="chico", codigo="AP-02", antipatron="avoidable_full_scan")
    grande = seed.problema(query_id="grande", codigo="AP-07", antipatron="disk_spill")
    empate_alta = seed.problema(query_id="empate_alta", codigo="AP-03", antipatron="non_sargable_predicate")
    empate_baja = seed.problema(query_id="empate_baja", codigo="AP-01", antipatron="baseline_degradation")
    abrazo = seed.problema(query_id=None, codigo="CT-01", antipatron="inconsistent_lock_order_deadlock", tabla=None)

    seed.deteccion(chico, impacto_ms=5)
    seed.deteccion(grande, impacto_ms=400)
    seed.deteccion(grande, impacto_ms=600)
    seed.deteccion(empate_alta, impacto_ms=50, severidad="high")
    seed.deteccion(empate_baja, impacto_ms=50, severidad="low")
    # Un abrazo mortal no trae impacto: cuenta como 0 y va despues de los medidos.
    seed.deteccion(abrazo, impacto_ms=None, severidad="high")

    findings = get(client).json()["diagnostics"]
    assert [f["id"] for f in findings] == [grande, empate_alta, empate_baja, chico, abrazo]
    assert findings[0]["impacto_sesion_ms"] == pytest.approx(1000)
    assert findings[-1]["impacto_sesion_ms"] is None


def test_sin_impacto_desempata_la_severidad_y_despues_el_codigo(client, seed):
    seed.base()
    ct02 = seed.problema(clave="b", codigo="CT-02", antipatron="lock_mode_conversion_deadlock")
    ct01 = seed.problema(clave="a", codigo="CT-01", antipatron="inconsistent_lock_order_deadlock")
    baja = seed.problema(clave="c", codigo="CT-01", antipatron="inconsistent_lock_order_deadlock", query_id="x")
    seed.deteccion(ct02, severidad="high")
    seed.deteccion(ct01, severidad="high")
    seed.deteccion(baja, severidad="low")
    assert [f["id"] for f in get(client).json()["diagnostics"]] == [ct01, ct02, baja]


def test_tope_de_problemas(client, seed, db_conn):
    seed.base()
    with db_conn.cursor() as cur:
        cur.execute(
            "WITH p AS (INSERT INTO public.problemas (db_id, antipatron, finding_key, severidad, evidencia, explicacion, recomendacion) "
            "  SELECT %s, 'missing_index', 'k' || g, 'low', '{}', 'e', 'r' FROM generate_series(1, %s) g RETURNING id) "
            "INSERT INTO public.detecciones (problema_id, severidad) SELECT id, 'low' FROM p",
            (DB, MAX_FINDINGS + 5),
        )
    assert len(get(client).json()["diagnostics"]) == MAX_FINDINGS


# --- estado de evaluacion -----------------------------------------------------------


def test_estado_de_la_ultima_ventana_y_conteo_de_la_sesion(client, seed):
    seed.base(sesion_hace=timedelta(hours=1))
    seed.estado("AP-02", "avoidable_full_scan", "evaluada", hasta_hace=timedelta(hours=2), ventanas=50)  # antes de la sesion
    seed.estado("AP-02", "avoidable_full_scan", "evaluada", hasta_hace=timedelta(minutes=30), ventanas=12)
    seed.estado("AP-02", "avoidable_full_scan", "parcial", hasta_hace=timedelta(minutes=1), ventanas=3)
    seed.estado("AP-01", "baseline_degradation", "evaluada", hasta_hace=timedelta(minutes=1))

    evaluation = get(client).json()["evaluation"]
    assert [e["codigo"] for e in evaluation] == ["AP-01", "AP-02"]
    ap02 = evaluation[1]
    assert ap02["estado"] == "parcial"
    assert ap02["ventanas_sesion"] == {"evaluada": 12, "parcial": 3}
    assert ap02["aplica"] is True


@pytest.mark.parametrize(("engine", "aplica"), [("postgresql", False), ("mysql", True)])
def test_ct03_y_ct04_solo_aplican_en_mysql(client, seed, engine, aplica):
    seed.base(engine=engine)
    seed.estado("CT-03", "range_contention_deadlock", "evaluada")
    seed.estado("CT-04", "lock_amplification_deadlock", "evaluada")
    seed.estado("CT-01", "inconsistent_lock_order_deadlock", "evaluada")
    by_code = {e["codigo"]: e["aplica"] for e in get(client).json()["evaluation"]}
    assert by_code == {"CT-01": True, "CT-03": aplica, "CT-04": aplica}


def test_extraccion_fallida_sin_estados_marca_todo_el_catalogo_no_evaluado(client, seed):
    seed.base()
    seed.last_run("failed")
    seed.issue("CONNECTION_FAILED", "blocking")
    seed.issue("STATS_RECENTLY_RESET", "info")

    evaluation = get(client).json()["evaluation"]
    assert [(e["codigo"], e["antipatron"]) for e in evaluation] == list(CATALOG)
    for e in evaluation:
        assert e["estado"] == "no_evaluada"
        # Solo los problemas bloqueantes abiertos son motivo.
        assert [m["codigo"] for m in e["motivos"]] == ["CONNECTION_FAILED"]
        assert e["motivos"][0] == {
            "codigo": "CONNECTION_FAILED",
            "origen": "recoleccion",
            "severidad": "blocking",
            "seccion": "statements",
            "mensaje": "mensaje de CONNECTION_FAILED",
            "accion": "haz esto",
        }


def test_extraccion_fallida_vuelve_no_evaluados_los_estados_anteriores(client, seed):
    seed.base()
    seed.estado("AP-02", "avoidable_full_scan", "evaluada", hasta_hace=timedelta(minutes=10))
    seed.estado("AP-01", "baseline_degradation", "evaluada", hasta_hace=timedelta(seconds=1))
    seed.last_run("failed", hace=timedelta(minutes=5))
    seed.issue("CONNECTION_FAILED", "blocking")

    by_code = {e["codigo"]: e for e in get(client).json()["evaluation"]}
    assert by_code["AP-02"]["estado"] == "no_evaluada"
    assert [m["codigo"] for m in by_code["AP-02"]["motivos"]] == ["CONNECTION_FAILED"]
    # Lo evaluado despues del fallo conserva su estado.
    assert by_code["AP-01"]["estado"] == "evaluada"
    assert by_code["AP-01"]["motivos"] == []


def test_extraccion_fallida_sin_problema_bloqueante_no_cambia_los_estados(client, seed):
    seed.base()
    seed.estado("AP-02", "avoidable_full_scan", "evaluada", hasta_hace=timedelta(minutes=10))
    seed.last_run("failed", hace=timedelta(minutes=5))
    assert get(client).json()["evaluation"][0]["estado"] == "evaluada"


def test_sin_fallo_y_sin_estados_la_evaluacion_viene_vacia(client, seed):
    seed.base()
    seed.last_run("ok")
    assert get(client).json()["evaluation"] == []


# --- actividad ---------------------------------------------------------------------


def test_actividad_por_hora_de_las_ultimas_24_horas(client, seed):
    seed.base(sesion_hace=timedelta(days=3))
    p = seed.problema()
    seed.deteccion(p, hace=timedelta(hours=30))  # fuera de la ventana de 24 h
    for _ in range(3):
        seed.deteccion(p, hace=timedelta(hours=2, minutes=10))
    seed.deteccion(p, hace=timedelta(minutes=1))

    activity = get(client).json()["activity"]
    assert sum(b["detecciones"] for b in activity) == 4
    horas = [datetime.fromisoformat(b["hora"]) for b in activity]
    assert horas == sorted(horas)
    assert all(h.minute == 0 and h.second == 0 for h in horas)


# --- salud del pipeline ----------------------------------------------------------------


def test_ultimo_ciclo(client, seed):
    seed.base()
    seed.last_run("degraded")
    last_run = get(client).json()["health"]["last_run"]
    assert last_run["status"] == "degraded"
    assert last_run["sections_ok"] == ["statements"]
    assert last_run["sections_failed"] == []


def test_problemas_de_salud_abiertos_y_resueltos_recientes(client, seed):
    seed.base()
    seed.issue("INFO_ABIERTO", "info", visto_hace=timedelta(seconds=1))
    seed.issue("BLOQUEANTE", "blocking", visto_hace=timedelta(minutes=30))
    seed.issue("DEGRADADO", "degraded", visto_hace=timedelta(minutes=10))
    seed.issue("RESUELTO_HOY", "blocking", resuelto_hace=timedelta(hours=1))
    seed.issue("RESUELTO_HACE_DIAS", "blocking", resuelto_hace=timedelta(hours=48))
    seed.issue("DE_OTRA_BASE", "blocking", db=OTRA)

    codes = [i["code"] for i in get(client).json()["health"]["issues"]]
    # Abiertos primero (bloqueante, degradado, informativo) y despues los resueltos.
    assert codes == ["BLOQUEANTE", "DEGRADADO", "INFO_ABIERTO", "RESUELTO_HOY"]

    codes_72h = [i["code"] for i in get(client, since_hours=72).json()["health"]["issues"]]
    assert "RESUELTO_HACE_DIAS" in codes_72h
    assert "DE_OTRA_BASE" not in codes_72h


# --- calidad de la captura de abrazos mortales ---------------------------------------


def test_sin_abrazos_ni_muestras_la_calidad_viene_en_cero(client, seed):
    seed.base()
    q = get(client).json()["calidad_deadlocks"]
    assert q["alcance"] == "base"
    assert q["hubo"] is None and q["perdidos"] is None  # sin contador no se sabe, no es 0
    assert q["capturados"] == q["con_detalle"] == q["sin_detalle"] == q["ciclos"] == 0
    assert q["por_patron"] == {}


def test_calidad_cuenta_hubo_capturados_y_perdidos(client, seed):
    seed.base()
    seed.muestra(hace=timedelta(minutes=3), deltas=2)
    seed.muestra(hace=timedelta(minutes=2), deltas=1)
    seed.abrazo("a|1")
    seed.abrazo("b|2", antipatron="lock_mode_conversion_deadlock")
    seed.abrazo("c|3", completo=False)  # encabezado sin detalle
    q = get(client).json()["calidad_deadlocks"]
    assert (q["hubo"], q["capturados"], q["perdidos"], q["capturados_de_mas"]) == (3, 3, 0, 0)
    assert (q["con_detalle"], q["sin_detalle"]) == (2, 1)
    assert q["por_patron"] == {
        "inconsistent_lock_order_deadlock": 1,
        "lock_mode_conversion_deadlock": 1,
    }
    assert q["ciclos"] == 2


def test_calidad_abrazo_perdido_y_capturado_de_mas(client, seed):
    seed.base()
    seed.muestra(deltas=3)
    seed.abrazo("a|1")
    q = get(client).json()["calidad_deadlocks"]
    assert (q["hubo"], q["capturados"], q["perdidos"], q["capturados_de_mas"]) == (3, 1, 2, 0)

    seed.abrazo("b|2")
    seed.abrazo("c|3")
    seed.abrazo("d|4")
    q = get(client).json()["calidad_deadlocks"]
    assert (q["perdidos"], q["capturados_de_mas"]) == (0, 1)


def test_calidad_reinicios_desbordes_y_huecos(client, seed):
    seed.base()
    seed.muestra(hace=timedelta(minutes=4), reset=True)
    seed.muestra(hace=timedelta(minutes=3), desborde=True)
    seed.muestra(hace=timedelta(minutes=2), segundos=300.0)
    q = get(client).json()["calidad_deadlocks"]
    assert (q["ciclos"], q["reinicios_contador"], q["ciclos_con_desborde"], q["huecos_largos"]) == (3, 1, 1, 1)


def test_calidad_alcance_es_servidor_en_mysql(client, seed):
    seed.base(engine="mysql")
    assert get(client).json()["calidad_deadlocks"]["alcance"] == "servidor"


def test_calidad_solo_cuenta_la_sesion_y_la_base_del_token(client, seed):
    seed.base(sesion_hace=timedelta(minutes=30))
    seed.base("db_otra")
    seed.muestra(hace=timedelta(hours=2), deltas=5)  # antes de la sesión
    seed.muestra(hace=timedelta(minutes=1), deltas=1)
    seed.muestra(db="db_otra", deltas=9)
    seed.abrazo("viejo|1", hace=timedelta(hours=2))
    seed.abrazo("a|1")
    seed.abrazo("ajeno|1", db="db_otra")
    q = get(client).json()["calidad_deadlocks"]
    assert (q["hubo"], q["capturados"], q["ciclos"]) == (1, 1, 1)


def test_calidad_sin_las_tablas_viene_nula_y_no_da_500(client, seed, db_conn):
    seed.base()
    with db_conn.cursor() as cur:
        cur.execute("ALTER TABLE public.deadlock_events RENAME TO deadlock_events_aparte")
    try:
        r = get(client)
        assert r.status_code == 200
        assert r.json()["calidad_deadlocks"] is None
    finally:
        with db_conn.cursor() as cur:
            cur.execute("ALTER TABLE public.deadlock_events_aparte RENAME TO deadlock_events")
