"""Modulo de contencion: bloqueos y abrazos mortales (CT-01 a CT-05).

Por ahora CT-01 (abrazo mortal por orden inconsistente de acceso, a partir de
snapshot.deadlocks) y CT-05 (cascada de bloqueo por transaccion larga, a partir
de active_queries). Lee el mismo snapshot que los antipatrones; process_job lo
llama con run_contention.
"""
from .facade import run_contention

__all__ = ["run_contention"]
