"""Modulo de contencion: bloqueos y abrazos mortales (CT-01 a CT-05).

CT-01 (abrazo mortal por orden inconsistente de acceso), CT-02 (por
conversion de modo), CT-03 (por contencion de rango, solo MySQL) y CT-04 (por
ampliacion de bloqueos sin indice, solo MySQL), a partir de
snapshot.deadlocks, con los abrazos que no se pueden distinguir entre CT-01 y
CT-02 como sin clasificar; y CT-05 (cascada de bloqueo por transaccion larga,
a partir de active_queries). Lee el mismo snapshot que los antipatrones; process_job lo
llama con run_contention.
"""
from .facade import run_contention

__all__ = ["run_contention"]
