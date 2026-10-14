"""Grafo de espera a partir de active_queries (blocking_pids).

Una arista va de la sesion que espera a la que la bloquea. blocking_pids no
siempre apunta a la raiz: en la cola de un mismo lock, Postgres y MySQL
reportan como bloqueadora a la sesion que esta delante en la cola, que a su
vez espera. Por eso la raiz y las sesiones afectadas se buscan recorriendo la
cadena completa, no solo las aristas directas.

Es la base comun de los patrones de contencion: CT-05 la usa para cadenas;
CT-01 a CT-04 (ciclos) la reutilizaran con los informes de abrazo mortal.
"""
from collections import deque
from dataclasses import dataclass, field
from collections.abc import Iterable

from querylens_contract import ActiveQueryRow


@dataclass
class WaitGraph:
    # pid -> fila del snapshot. Una bloqueadora puede no tener fila (ej. un
    # proceso de fondo que el colector no reporta): aparece en las aristas igual
    sessions: dict[int, ActiveQueryRow] = field(default_factory=dict)
    # pid que espera -> pids que lo bloquean directamente
    blockers: dict[int, set[int]] = field(default_factory=dict)
    # pid bloqueador -> pids que lo esperan directamente
    waiters: dict[int, set[int]] = field(default_factory=dict)

    def roots(self) -> list[int]:
        """Sesiones que bloquean a alguien y no esperan a nadie: el inicio de
        cada cadena. En un ciclo puro (abrazo mortal) no hay raiz."""
        return sorted(pid for pid in self.waiters if not self.blockers.get(pid))

    def blocked_under(self, root: int) -> set[int]:
        """Todas las sesiones que esperan a root, directa o indirectamente."""
        seen: set[int] = set()
        queue = deque(self.waiters.get(root, ()))
        while queue:
            pid = queue.popleft()
            # el visitado corta ciclos: una cadena puede colgar de un abrazo mortal
            if pid in seen or pid == root:
                continue
            seen.add(pid)
            queue.extend(self.waiters.get(pid, ()))
        return seen


def build_wait_graph(active_queries: Iterable[ActiveQueryRow]) -> WaitGraph:
    graph = WaitGraph()
    for row in active_queries:
        graph.sessions[row.process_id] = row
        for blocker in row.blocking_pids:
            if blocker == row.process_id:
                continue
            graph.blockers.setdefault(row.process_id, set()).add(blocker)
            graph.waiters.setdefault(blocker, set()).add(row.process_id)
    return graph
