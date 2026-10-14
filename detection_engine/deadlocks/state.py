"""Estado de contencion que CT-05 arrastra de un snapshot al siguiente.

Una fila por db_id en public.contention_state. Guarda las raices que cumplieron
la regla en la ultima muestra procesada y tambien las de la muestra anterior a
esa: si el job se reintenta (el hallazgo no llego a guardarse), se recalcula
contra la misma muestra previa y sale el mismo resultado.
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from anti_patterns.statement_history import parse_instant

_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "querylens_database" / "contention_state.sql"

# Dos muestras son consecutivas si no hay mas que esto entre ellas. Con el
# pipeline caido un rato, la muestra anterior ya no dice nada de la actual.
MAX_SAMPLE_GAP = timedelta(seconds=60)


@dataclass
class CascadeRoot:
    """Una raiz que cumplio la regla de CT-05 en una muestra."""

    pid: int
    # como llego en el snapshot: junto con pid identifica la transaccion, por
    # si el motor reutiliza el pid en otra sesion
    transaction_start_time: str
    # primera muestra en que cumplio la regla
    first_seen_at: datetime
    # ya se emitio el hallazgo de esta raiz: no se repite en cada muestra
    reported: bool = False

    @property
    def key(self) -> tuple[int, str]:
        return (self.pid, self.transaction_start_time)


RootsByKey = dict[tuple[int, str], CascadeRoot]


@dataclass
class ContentionState:
    captured_at: datetime | None = None
    roots: RootsByKey = field(default_factory=dict)
    prev_captured_at: datetime | None = None
    prev_roots: RootsByKey = field(default_factory=dict)

    def previous_for(self, captured_at: datetime) -> RootsByKey | None:
        """Las raices de la muestra anterior a captured_at, o None si el
        snapshot es mas viejo que el ultimo procesado (llego fuera de orden)."""
        if self.captured_at is None:
            return {}
        if captured_at == self.captured_at:
            # reintento del mismo job: se compara contra la misma muestra previa
            return _consecutive(self.prev_roots, self.prev_captured_at, captured_at)
        if captured_at < self.captured_at:
            return None
        return _consecutive(self.roots, self.captured_at, captured_at)

    def advance(self, captured_at: datetime, roots: RootsByKey) -> "ContentionState":
        if captured_at == self.captured_at:
            return ContentionState(captured_at, roots, self.prev_captured_at, self.prev_roots)
        return ContentionState(captured_at, roots, self.captured_at, self.roots)


def _consecutive(roots: RootsByKey, at: datetime | None, captured_at: datetime) -> RootsByKey:
    if at is None or captured_at - at > MAX_SAMPLE_GAP:
        return {}
    return roots


def _dump_roots(roots: RootsByKey) -> str:
    return json.dumps([asdict(root) for root in roots.values()], default=str)


def _load_roots(raw) -> RootsByKey:
    items = raw if isinstance(raw, list) else json.loads(raw or "[]")
    roots = [
        CascadeRoot(
            pid=item["pid"],
            transaction_start_time=item["transaction_start_time"],
            first_seen_at=parse_instant(item["first_seen_at"]),
            reported=item["reported"],
        )
        for item in items
    ]
    return {root.key: root for root in roots}


def ensure_contention_state_table(engine: Engine) -> None:
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def load_contention_state(engine: Engine, db_id: str) -> ContentionState:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT captured_at, roots, prev_captured_at, prev_roots
                FROM public.contention_state
                WHERE db_id = :db_id
                """
            ),
            {"db_id": db_id},
        ).mappings().first()
    if row is None:
        return ContentionState()
    return ContentionState(
        captured_at=row["captured_at"],
        roots=_load_roots(row["roots"]),
        prev_captured_at=row["prev_captured_at"],
        prev_roots=_load_roots(row["prev_roots"]),
    )


def save_contention_state(engine: Engine, db_id: str, state: ContentionState) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.contention_state
                    (db_id, captured_at, roots, prev_captured_at, prev_roots)
                VALUES
                    (:db_id, :captured_at, CAST(:roots AS JSONB),
                     :prev_captured_at, CAST(:prev_roots AS JSONB))
                ON CONFLICT (db_id) DO UPDATE SET
                    captured_at = EXCLUDED.captured_at,
                    roots = EXCLUDED.roots,
                    prev_captured_at = EXCLUDED.prev_captured_at,
                    prev_roots = EXCLUDED.prev_roots
                -- un job viejo que termina despues de uno nuevo no pisa el estado
                WHERE public.contention_state.captured_at <= EXCLUDED.captured_at
                """
            ),
            {
                "db_id": db_id,
                "captured_at": state.captured_at,
                "roots": _dump_roots(state.roots),
                "prev_captured_at": state.prev_captured_at,
                "prev_roots": _dump_roots(state.prev_roots),
            },
        )
