"""El pipeline real como daemon, en su propio proceso (subproceso del controlador).

    python -m overhead.pipeline_proc --engine postgres --cadence 10 --events ciclos.jsonl

Igual que en produccion (runner.py) el pipeline vive en otro proceso que la
carga y cicla con su cadencia: un ciclo es runner.Runner.cycle() sobre un solo
Target del sandbox (main_sandbox.ENGINES), y cada target corre main.run_engine
(collect -> explain -> encolado en PGMQ -> pipeline_health). El proceso queda
vivo entre bloques: imports, cache del preflight y pools quedan calientes,
como en un daemon que lleva horas arriba.

Protocolo por stdin (una orden por linea), respuesta por el stdout original:
    on <delay_s>  -> "ack on"; primer ciclo a los delay_s, luego cada cadencia
    off           -> "ack off" cuando termino el ciclo en curso (si habia uno):
                     al recibir el ack ningun ciclo puede pisar el bloque siguiente
    quit          -> "ack quit" y sale
El stdout del pipeline (main imprime "Diccionario encolado ...") se desvia a
stderr para no mezclarse con el protocolo.

--mode burn reemplaza el pipeline por BURN_CONCURRENCY copias concurrentes de
workload.BURN_QUERIES con la misma cadencia: es el control positivo, un
sobrecosto que el arnes tiene que ver.
"""
import argparse
import json
import os
import sys
import threading
import time

from sqlalchemy import text

import main as pipeline_main
from config.registered import Target
from main_sandbox import ENGINES
from overhead.workload import BURN_QUERIES
from runner import Runner
from stages import auto_explain as auto_explain_stage

BURN_CONCURRENCY = 3

# Lo que el ciclo en curso dejo en los hooks (se reinicia en cada ciclo).
_cycle_info = {}


def _install_hooks():
    """Cuenta candidatos, planes del log y bytes leidos del log por ciclo.

    Envuelve las funciones reales sin cambiar su resultado: es evidencia de que
    el ciclo medido es el pipeline actual (AutoExplainStage leyendo el log) y no
    uno degradado al plan generico."""
    original_execute = auto_explain_stage.AutoExplainStage.execute
    original_collect = auto_explain_stage._collect_plans

    def execute(self, stats, conn):
        result = original_execute(self, stats, conn)
        _cycle_info["candidates"] = sum(
            1 for c in stats.get("top_impact_queries") or [] if c.get("ready_for_explain")
        )
        _cycle_info["auto_explain_plans"] = len(stats.get("auto_explain_plans") or {})
        return result

    def collect(chunk, *args, **kwargs):
        _cycle_info["log_bytes_read"] = _cycle_info.get("log_bytes_read", 0) + len(chunk)
        return original_collect(chunk, *args, **kwargs)

    auto_explain_stage.AutoExplainStage.execute = execute
    auto_explain_stage._collect_plans = collect


class Daemon:
    def __init__(self, engine, cadence, mode, events_path, proto):
        self.engine = engine
        self.cadence = cadence
        self.mode = mode
        self.proto = proto
        self.events = open(events_path, "a", encoding="utf-8")
        self.factory = dict(ENGINES)[engine]
        self.active = threading.Event()
        self.quit = threading.Event()
        self.cycle_lock = threading.Lock()
        self.first_at = 0.0
        self.runner = Runner(
            interval=cadence,
            load=lambda: [Target(engine, self.factory)],
            run=self._run_targets,
        )
        self._burn_engine = self.factory() if mode == "burn" else None

    def _write_event(self, event):
        self.events.write(json.dumps(event) + "\n")
        self.events.flush()

    def _run_targets(self, targets):
        for target in targets:
            _cycle_info.clear()
            ok, error = True, None
            t0 = time.monotonic_ns()
            try:
                if self.mode == "burn":
                    self._burn()
                else:
                    pipeline_main.run_engine(target.dialect, target.factory, target.db_id)
            except Exception as e:
                ok, error = False, f"{type(e).__name__}: {str(e)[:200]}"
            t1 = time.monotonic_ns()
            self._write_event({"kind": "cycle", "mode": self.mode, "t0_ns": t0, "t1_ns": t1,
                               "ok": ok, "error": error, **_cycle_info})

    def _burn(self):
        """BURN_CONCURRENCY copias de la query pesada a la vez: una sola entra en
        un core libre del host y no frena a nadie; el control tiene que competir."""
        errors = []

        def one():
            try:
                with self._burn_engine.connect() as conn:
                    conn.execute(text(BURN_QUERIES[self.engine])).fetchall()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=one) for _ in range(BURN_CONCURRENCY)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]

    def loop(self):
        while not self.quit.is_set():
            if not self.active.is_set() or time.monotonic() < self.first_at:
                time.sleep(0.02)
                continue
            started = time.monotonic()
            with self.cycle_lock:
                # Re-chequeo dentro del lock: un "off" que llego entre el if y
                # el lock ya tiene que haber cortado.
                if not self.active.is_set():
                    continue
                self.runner.cycle()
            # Cadencia anclada al inicio del ciclo, como Runner.run_forever.
            while (self.active.is_set() and not self.quit.is_set()
                   and time.monotonic() < started + self.cadence):
                time.sleep(0.02)

    def reply(self, msg):
        self.proto.write(msg + "\n")
        self.proto.flush()

    def serve(self):
        worker = threading.Thread(target=self.loop, daemon=True)
        worker.start()
        self.reply("ready")
        for line in sys.stdin:
            parts = line.split()
            if not parts:
                continue
            cmd = parts[0]
            if cmd == "on":
                delay = float(parts[1]) if len(parts) > 1 else 0.0
                self.first_at = time.monotonic() + delay
                self.active.set()
                self.reply("ack on")
            elif cmd == "off":
                self.active.clear()
                # Espera el ciclo en curso: despues del ack no corre ninguno.
                with self.cycle_lock:
                    pass
                self.reply("ack off")
            elif cmd == "quit":
                break
        self.active.clear()
        self.quit.set()
        with self.cycle_lock:
            pass
        self.events.close()
        if self._burn_engine is not None:
            self._burn_engine.dispose()
        self.reply("ack quit")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=[e for e, _ in ENGINES], required=True)
    parser.add_argument("--cadence", type=float, default=10.0)
    parser.add_argument("--mode", choices=["pipeline", "burn"], default="pipeline")
    parser.add_argument("--events", required=True)
    args = parser.parse_args(argv)

    # El protocolo usa una copia del stdout original; el resto va a stderr.
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr

    _install_hooks()
    Daemon(args.engine, args.cadence, args.mode, args.events, proto).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
