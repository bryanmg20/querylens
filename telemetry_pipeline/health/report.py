"""Acumulador en memoria de la salud de UNA corrida contra una base.

Los stages agregan issues y marcan que scopes evaluaron; main.run_engine lo
vuelca al final con writer.flush. No toca la base: probarlo no necesita fakes.
"""
from health.catalog import BLOCKING, CATALOG, DEGRADED

_RECORDED_ATTR = "_health_recorded"


def mark_recorded(exc: BaseException) -> None:
    """El punto que registra el code de una excepcion y la relanza la marca,
    para que run_engine no la cuente ademas como PIPELINE_FAILED."""
    try:
        setattr(exc, _RECORDED_ATTR, True)
    except AttributeError:
        pass


def is_recorded(exc: BaseException) -> bool:
    return bool(getattr(exc, _RECORDED_ATTR, False))


class HealthReport:
    def __init__(self):
        # (code, section) -> params. Un mismo problema repetido en la corrida
        # (p. ej. EXPLAIN denegado en 5 candidatos) es UNA fila con count.
        self.issues: dict[tuple[str, str], dict] = {}
        self.scopes: set[str] = set()
        self.sections_ok: list[str] = []
        self.sections_failed: list[str] = []
        self.engine_version: str | None = None
        self.msg_id: int | None = None
        # Datos del preflight que usan los chequeos post-collect (no se persisten).
        self.facts: dict = {}

    def add(self, code: str, section: str = "", **params) -> None:
        if code not in CATALOG:
            raise KeyError(f"code fuera del catalogo: {code!r}")
        key = (code, section or "")
        current = self.issues.get(key)
        if current is None:
            self.issues[key] = {**params, "count": 1}
        else:
            current.update(params)
            current["count"] += 1

    def discard(self, code: str, section: str = "") -> None:
        self.issues.pop((code, section or ""), None)

    def checked(self, *scopes: str) -> None:
        self.scopes.update(scopes)

    def section_ok(self, name: str) -> None:
        self.sections_ok.append(name)

    def section_failed(self, name: str) -> None:
        self.sections_failed.append(name)

    def has(self, code: str) -> bool:
        return any(c == code for c, _ in self.issues)

    def status(self) -> str:
        severities = {CATALOG[code].severity for code, _ in self.issues}
        if BLOCKING in severities:
            return "failed"
        if DEGRADED in severities:
            return "degraded"
        return "ok"
