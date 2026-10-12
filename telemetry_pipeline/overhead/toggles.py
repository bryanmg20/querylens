"""Prende/apaga la instrumentacion de servidor que el pipeline necesita.

Es el brazo "instrumentacion" de la medicion: cuanto le cuesta a la carga la
configuracion que QueryLens le pide al cliente, sin el pipeline corriendo. Se
reporta aparte del veredicto del 5 %.

  * Postgres: auto_explain.log_min_duration (guia 006) de las sesiones de la
    carga. Apagado = -1 via ALTER ROLE (ver PgAutoExplain): no hace falta
    reiniciar.
  * MySQL: solo los consumers de performance_schema que el pipeline lee
    (health/preflight.MY_CONSUMERS sin los de jerarquia): statements_digest
    (STATEMENTS_QUERY) y events_statements_current (ACTIVE_QUERIES_QUERY). Los
    instrumentos y performance_schema=ON quedan: apagarlos exige reinicio.

Todo cambio se deshace en close() (el controlador lo llama en un finally,
tambien con Ctrl+C) y, por si el proceso muere de otra forma, con atexit. El estado restaurado se verifica.
"""
import atexit

from psycopg2 import sql

from overhead.connect import LOAD_APPLICATION_NAME, my_connect, pg_connect, pg_user

PG_SETTING = "auto_explain.log_min_duration"
MY_CONSUMERS = ("statements_digest", "events_statements_current")


class PgAutoExplain:
    """auto_explain.log_min_duration de las sesiones de la carga.

    ALTER SYSTEM no sirve: un -c de la linea de comando (el compose del
    sandbox) gana sobre postgresql.auto.conf. Un setting de rol si gana, pero
    solo en sesiones nuevas: tras cada cambio se terminan las sesiones de
    overhead.loadgen, que reconectan solas. Se hace en los dos sentidos (off y
    on), asi la reconexion pesa igual en ambos lados del par.
    """

    def __init__(self):
        self.conn = pg_connect("ADMIN")
        self.role = pg_user("LOAD")
        with self.conn.cursor() as cur:
            cur.execute("SELECT setting::int FROM pg_settings WHERE name = %s", (PG_SETTING,))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"{PG_SETTING} no existe: auto_explain no esta cargado")
            self.server_value = row[0]
            # Un setting previo del rol (de otra prueba) se respeta al restaurar.
            cur.execute(
                "SELECT split_part(cfg, '=', 2) FROM pg_db_role_setting s "
                "JOIN pg_roles r ON r.oid = s.setrole, unnest(s.setconfig) AS cfg "
                "WHERE r.rolname = %s AND s.setdatabase = 0 AND cfg LIKE %s",
                (self.role, PG_SETTING + "=%"),
            )
            row = cur.fetchone()
        self.role_original = row[0] if row else None
        self.changed = False
        atexit.register(self.restore)

    def describe(self):
        return {"setting": PG_SETTING, "role": self.role, "server_value_ms": self.server_value,
                "role_original": self.role_original}

    def _load_value(self):
        conn = pg_connect("LOAD")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT setting::int FROM pg_settings WHERE name = %s", (PG_SETTING,))
                return cur.fetchone()[0]
        finally:
            conn.close()

    def _apply(self, value, expected):
        role = sql.Identifier(self.role)
        with self.conn.cursor() as cur:
            if value is None:
                cur.execute(sql.SQL("ALTER ROLE {} RESET " + PG_SETTING).format(role))
            else:
                cur.execute(sql.SQL("ALTER ROLE {} SET " + PG_SETTING + " = {}").format(
                    role, sql.Literal(str(value))))
            cur.execute(
                "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                "WHERE usename = %s AND application_name = %s",
                (self.role, LOAD_APPLICATION_NAME),
            )
        if self._load_value() != expected:
            raise RuntimeError(f"{PG_SETTING} no tomo el valor {expected} en el rol {self.role}")

    def off(self):
        self.changed = True
        self._apply(-1, -1)

    def on(self):
        expected = int(self.role_original) if self.role_original else self.server_value
        self._apply(self.role_original, expected)

    def restore(self):
        if self.changed:
            self.on()
            self.changed = False

    def close(self):
        self.restore()
        self.conn.close()


class MyConsumers:
    def __init__(self):
        self.conn = my_connect("ADMIN")
        self.original = self._read()
        missing = set(MY_CONSUMERS) - set(self.original)
        if missing:
            raise RuntimeError(f"setup_consumers sin {sorted(missing)}")
        self.changed = False
        atexit.register(self.restore)

    def describe(self):
        return {"consumers": dict(self.original)}

    def _read(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT NAME, ENABLED FROM performance_schema.setup_consumers WHERE NAME IN %s",
                (MY_CONSUMERS,),
            )
            return {name: enabled for name, enabled in cur.fetchall()}

    def _apply(self, state):
        with self.conn.cursor() as cur:
            for name, enabled in state.items():
                cur.execute(
                    "UPDATE performance_schema.setup_consumers SET ENABLED = %s WHERE NAME = %s",
                    (enabled, name),
                )
        if self._read() != state:
            raise RuntimeError(f"setup_consumers no quedo en {state}")

    def off(self):
        self.changed = True
        self._apply({name: "NO" for name in MY_CONSUMERS})

    def on(self):
        self._apply(self.original)

    def restore(self):
        if self.changed:
            self._apply(self.original)
            self.changed = False

    def close(self):
        self.restore()
        self.conn.close()


TOGGLES = {"postgres": PgAutoExplain, "mysql": MyConsumers}
