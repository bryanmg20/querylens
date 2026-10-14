"""Modulo de contencion: bloqueos y abrazos mortales (CT-01 a CT-05).

Por ahora solo CT-05 (cascada de bloqueo por transaccion larga). Lee el mismo
snapshot que los antipatrones; process_job lo llama con run_contention.
"""
from .facade import run_contention

__all__ = ["run_contention"]
