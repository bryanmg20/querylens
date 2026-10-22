"""Estado de contencion que el modulo arrastra de un snapshot al siguiente.

Una fila por db_id en public.contention_state. Guarda las raices que cumplieron
la regla de CT-05 en la ultima muestra procesada y los abrazos mortales ya
vistos (CT-01, CT-02), y tambien los de la muestra anterior a esa: si el job se
reintenta (el hallazgo no llego a guardarse), se recalcula contra la misma
muestra previa y sale el mismo resultado.
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

# Cuanto se recuerda un abrazo ya visto. Cada snapshot trae los de los ultimos
# 120 s (DeadlocksStage.LOOKBACK_S): con margen para un pipeline atrasado.
DEADLOCK_SEEN_RETENTION = timedelta(minutes=10)
# Una firma que no se repite en este tiempo vuelve a contar desde cero.
SIGNATURE_RETENTION = timedelta(days=7)


@dataclass
class DeadlockMemory:
    """Abrazos mortales ya procesados y cuantas veces aparecio cada firma."""

    # detected_at -> firma ("" si el informe vino sin detalle). El mismo
    # informe llega en varios snapshots seguidos.
    seen: dict[str, str] = field(default_factory=dict)
    # firma -> {"count", "first_seen_at", "last_seen_at"} (isoformat)
    signatures: dict[str, dict] = field(default_factory=dict)

    def pruned(self, captured_at: datetime) -> "DeadlockMemory":
        seen_after = captured_at - DEADLOCK_SEEN_RETENTION
        signature_after = captured_at - SIGNATURE_RETENTION
        return DeadlockMemory(
            seen={
                detected_at: signature for detected_at, signature in self.seen.items()
                if _after(detected_at, seen_after)
            },
            signatures={
                signature: info for signature, info in self.signatures.items()
                if _after(info["last_seen_at"], signature_after)
            },
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw) -> "DeadlockMemory":
        data = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        return cls(seen=dict(data.get("seen") or {}), signatures=dict(data.get("signatures") or {}))


def _after(value: str, limit: datetime) -> bool:
    instant = parse_instant(value)
    return instant is not None and instant >= limit


@dataclass
class ContentionState:
    captured_at: datetime | None = None
    roots: RootsByKey = field(default_factory=dict)
    prev_captured_at: datetime | None = None
    prev_roots: RootsByKey = field(default_factory=dict)
    deadlocks: DeadlockMemory = field(default_factory=DeadlockMemory)
    prev_deadlocks: DeadlockMemory = field(default_factory=DeadlockMemory)

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

    def deadlocks_for(self, captured_at: datetime) -> DeadlockMemory:
        """Lo visto antes de captured_at. Sin tope de separacion entre
        muestras, a diferencia de previous_for: con el pipeline caido un rato,
        un abrazo ya informado sigue sin repetirse."""
        if captured_at == self.captured_at:
            return self.prev_deadlocks
        return self.deadlocks

    def advance(
        self, captured_at: datetime, roots: RootsByKey, deadlocks: DeadlockMemory | None = None
    ) -> "ContentionState":
        if captured_at == self.captured_at:
            return ContentionState(
                captured_at, roots, self.prev_captured_at, self.prev_roots,
                deadlocks if deadlocks is not None else self.deadlocks, self.prev_deadlocks,
            )
        return ContentionState(
            captured_at, roots, self.captured_at, self.roots,
            deadlocks if deadlocks is not None else self.deadlocks, self.deadlocks,
        )


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
                SELECT captured_at, roots, prev_captured_at, prev_roots,
                       deadlocks, prev_deadlocks
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
        deadlocks=DeadlockMemory.from_json(row["deadlocks"]),
        prev_deadlocks=DeadlockMemory.from_json(row["prev_deadlocks"]),
    )


def save_contention_state(engine: Engine, db_id: str, state: ContentionState) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.contention_state
                    (db_id, captured_at, roots, prev_captured_at, prev_roots,
                     deadlocks, prev_deadlocks)
                VALUES
                    (:db_id, :captured_at, CAST(:roots AS JSONB),
                     :prev_captured_at, CAST(:prev_roots AS JSONB),
                     CAST(:deadlocks AS JSONB), CAST(:prev_deadlocks AS JSONB))
                ON CONFLICT (db_id) DO UPDATE SET
                    captured_at = EXCLUDED.captured_at,
                    roots = EXCLUDED.roots,
                    prev_captured_at = EXCLUDED.prev_captured_at,
                    prev_roots = EXCLUDED.prev_roots,
                    deadlocks = EXCLUDED.deadlocks,
                    prev_deadlocks = EXCLUDED.prev_deadlocks
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
                "deadlocks": state.deadlocks.to_json(),
                "prev_deadlocks": state.prev_deadlocks.to_json(),
            },
        )
