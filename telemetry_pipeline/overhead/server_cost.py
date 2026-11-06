"""Snapshots de costo en el servidor, tomados en cada borde de bloque.

Todo es acumulado: el costo de un bloque es (despues - antes). Va como
evidencia secundaria; el veredicto sale de la latencia de la carga.

  * CPU del contenedor: usage_usec de cgroup v2 (CPU-segundos exactos), en
    lugar de muestrear `docker stats` cada 1,5 s.
  * Postgres: pg_stat_statements del rol querylens_monitor (no un LIKE sobre
    el texto) y tamano del log JSON que AutoExplainStage relee cada ciclo.
  * MySQL: events_statements_summary_by_user_by_event_name del usuario
    querylens_monitor (en lugar de prefijos de PIPELINE_FINGERPRINT, que
    tambien cuentan sentencias de otros usuarios).
"""
import subprocess

MONITOR_USER = "querylens_monitor"
CONTAINERS = {"postgres": "ql_postgres", "mysql": "ql_mysql"}

PG_MONITOR_SQL = """
SELECT coalesce(sum(s.calls), 0)::bigint,
       coalesce(sum(s.total_exec_time), 0)::float8,
       coalesce(sum(s.shared_blks_hit + s.shared_blks_read), 0)::bigint,
       coalesce(sum(s.temp_blks_written), 0)::bigint
FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
WHERE r.rolname = %s
"""
PG_LOG_SQL = "SELECT coalesce(sum(size), 0)::bigint FROM pg_ls_logdir() WHERE name LIKE '%%.json'"

MY_MONITOR_SQL = """
SELECT coalesce(sum(COUNT_STAR), 0), coalesce(sum(SUM_TIMER_WAIT), 0),
       coalesce(sum(SUM_ROWS_EXAMINED), 0)
FROM performance_schema.events_statements_summary_by_user_by_event_name
WHERE USER = %s
"""


def container_cpu_usec(container):
    """usage_usec acumulado del cgroup del contenedor, o None."""
    try:
        out = subprocess.run(
            ["docker", "exec", container, "cat", "/sys/fs/cgroup/cpu.stat"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            return int(value)
    return None


def pg_snapshot(admin_conn):
    with admin_conn.cursor() as cur:
        cur.execute(PG_MONITOR_SQL, (MONITOR_USER,))
        calls, exec_ms, blks, temp = cur.fetchone()
        cur.execute(PG_LOG_SQL)
        log_bytes = cur.fetchone()[0]
    return {"monitor_calls": int(calls), "monitor_exec_ms": float(exec_ms),
            "monitor_blks": int(blks), "monitor_temp_blks_written": int(temp),
            "log_json_bytes": int(log_bytes)}


def my_snapshot(admin_conn):
    with admin_conn.cursor() as cur:
        cur.execute(MY_MONITOR_SQL, (MONITOR_USER,))
        calls, timer_ps, rows = cur.fetchone()
    # SUM_TIMER_WAIT esta en picosegundos.
    return {"monitor_calls": int(calls), "monitor_exec_ms": float(timer_ps) / 1e9,
            "monitor_rows_examined": int(rows)}


SNAPSHOT = {"postgres": pg_snapshot, "mysql": my_snapshot}


def snapshot(engine, admin_conn):
    row = {"cpu_usec": container_cpu_usec(CONTAINERS[engine])}
    try:
        row.update(SNAPSHOT[engine](admin_conn))
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return row


def delta(before, after):
    """after - before de cada campo numerico presente en ambos."""
    out = {}
    for key, b in before.items():
        a = after.get(key)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            out[key] = a - b
    return out
