"""Ventana final del log JSON de Postgres, compartida por los stages que lo leen.

AutoExplainStage (planes) y DeadlocksStage (informes 40P01) leen el mismo log
con querylens.log_files() / querylens.read_log() (guia references/006).
PgLogTail lo lee una sola vez por ciclo: del archivo mas nuevo al mas viejo,
hasta LOG_WINDOW_BYTES repartidos entre archivos, y solo cuando alguien pide el
archivo siguiente. El que llega segundo recorre lo ya leido sin volver a
consultar. Si una lectura falla, no se recuerda el error: el consumidor
siguiente reintenta desde el archivo que fallo, en su propia transaccion.

No hay estado entre ciclos: el orquestador crea un PgLogTail por ciclo y cada
ciclo relee la ventana.
"""
from collections.abc import Iterator
from datetime import datetime

from querylens_contract import to_naive_utc
from sqlalchemy import text

LOG_FILES_SQL = "SELECT name, size, modification FROM querylens.log_files() ORDER BY modification DESC"
READ_LOG_SQL = "SELECT querylens.read_log(:name, :start_offset, :max_bytes) AS chunk"

# Bytes que se leen por ciclo. Un plan JSON de auto_explain ocupa unos 2-6 KB,
# asi que entran varios cientos; un informe de abrazo mortal, menos de 1 KB.
LOG_WINDOW_BYTES = 2 * 1024 * 1024


class PgLogTail:
    def __init__(self):
        # archivos listados que todavia no se leyeron (None: falta listarlos)
        self._pending: list | None = None
        self._budget = 0
        # (ultima modificacion, lineas completas) de cada archivo ya leido,
        # del mas nuevo al mas viejo
        self._read: list[tuple[datetime | None, list[bytes]]] = []
        # bytes pedidos a read_log en este ciclo (evidencia del sobrecosto)
        self.bytes_read = 0

    def files(self, conn, modified_since: datetime | None = None) -> Iterator[list[bytes]]:
        """Lineas completas de cada archivo, del mas nuevo al mas viejo; dentro
        de cada archivo, en el orden en que se escribieron. Quien corta la
        iteracion antes no gasta el resto del presupuesto.

        modified_since (UTC sin zona): corta en el primer archivo que no se
        modifico desde entonces; los que siguen son mas viejos todavia."""
        index = 0
        while True:
            if index < len(self._read):
                modified, lines = self._read[index]
                if _older(modified, modified_since):
                    return
                yield lines
                index += 1
            elif not self._read_next(conn, modified_since):
                return

    def _read_next(self, conn, modified_since: datetime | None) -> bool:
        if self._pending is None:
            self._pending = [dict(row) for row in conn.execute(text(LOG_FILES_SQL)).mappings()]
            self._budget = LOG_WINDOW_BYTES
        while self._pending and self._budget > 0:
            log_file = self._pending[0]
            modified = log_file.get("modification")
            if _older(modified, modified_since):
                # sin consumirlo: otro consumidor sin corte por fecha puede seguir
                return False
            size = int(log_file["size"] or 0)
            if size <= 0:
                self._pending.pop(0)
                continue
            length = min(size, self._budget)
            start_offset = size - length
            row = next(iter(conn.execute(
                text(READ_LOG_SQL).bindparams(
                    name=log_file["name"], start_offset=start_offset, max_bytes=length
                )
            ).mappings()), None)
            # Recien aca se consume: si read_log lanzo, el archivo queda para
            # el reintento.
            self._pending.pop(0)
            chunk = row["chunk"] if row is not None else None
            # NULL: el archivo roto entre listar y leer.
            if chunk is None:
                continue
            self._budget -= length
            self.bytes_read += length
            lines = bytes(chunk).split(b"\n")
            if start_offset > 0:
                # La ventana empieza en medio de una linea (o de un
                # caracter UTF-8): ese pedazo no es JSON valido.
                lines = lines[1:]
            self._read.append((modified, lines))
            return True
        return False


def _older(modified, since: datetime | None) -> bool:
    return since is not None and modified is not None and to_naive_utc(modified) < since
