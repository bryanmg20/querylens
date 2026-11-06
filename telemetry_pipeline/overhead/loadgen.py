"""Generador de la carga observada (subproceso del controlador).

    python -m overhead.loadgen --engine postgres --workers 2 --out muestras.csv

Cada worker es un hilo con UNA conexion persistente que repite las plantillas
de workload.QUERIES en lazo cerrado. A diferencia de continuous_load.sh (un
psql/mysql por query), la latencia no incluye fork + TCP + auth: mide al
servidor. Corre hasta SIGTERM/SIGINT y escribe una linea por query:

    t0_ns,t1_ns,template_id,ok

con time.monotonic_ns (CLOCK_MONOTONIC: el mismo reloj en todos los procesos
del host), que es lo que usa el controlador para cortar los bloques.
"""
import argparse
import signal
import sys
import threading
import time

from overhead.connect import CONNECT
from overhead.workload import QUERIES

FLUSH_EVERY_S = 1.0
RECONNECT_WAIT_S = 0.5


def _worker(engine, index, stop, out, lock):
    conn = None
    # Cada worker arranca en otra plantilla: los dos no piden lo mismo a la vez.
    tid = index % len(QUERIES)
    while not stop.is_set():
        if conn is None:
            try:
                conn = CONNECT[engine]("LOAD")
            except Exception as e:
                print(f"loadgen | worker {index} | connect | {e}", file=sys.stderr)
                stop.wait(RECONNECT_WAIT_S)
                continue
        ok = 1
        t0 = time.monotonic_ns()
        try:
            with conn.cursor() as cur:
                cur.execute(QUERIES[tid])
                cur.fetchall()
        except Exception as e:
            ok = 0
            print(f"loadgen | worker {index} | t{tid} | {type(e).__name__}: {e}",
                  file=sys.stderr)
            try:
                conn.close()
            except Exception:
                pass
            conn = None
        t1 = time.monotonic_ns()
        with lock:
            out.append(f"{t0},{t1},{tid},{ok}\n")
        tid = (tid + 1) % len(QUERIES)
    if conn is not None:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=sorted(CONNECT), required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    buffer, lock = [], threading.Lock()
    threads = [
        threading.Thread(target=_worker, args=(args.engine, i, stop, buffer, lock), daemon=True)
        for i in range(args.workers)
    ]
    for t in threads:
        t.start()

    with open(args.out, "a", encoding="utf-8") as f:
        while not stop.is_set():
            stop.wait(FLUSH_EVERY_S)
            with lock:
                lines, buffer[:] = list(buffer), []
            f.writelines(lines)
            f.flush()
        for t in threads:
            t.join(timeout=60)
        with lock:
            f.writelines(buffer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
