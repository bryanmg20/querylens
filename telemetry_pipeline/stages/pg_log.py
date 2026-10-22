"""Ventana final del log JSON de Postgres, compartida por los stages que lo leen.

AutoExplainStage (planes) y DeadlocksStage (informes 40P01) leen el mismo log
con querylens.log_files() / querylens.read_log() (guia references/006).
PgLogTail lo lee una sola vez por ciclo: del archivo mas nuevo al mas viejo,
hasta LOG_WINDOW_BYTES repartidos entre archivos, y solo cuando alguien pide el
archivo siguiente. El que llega segundo recorre lo ya leido sin volver a
consultar, y si la lectura fallo, recibe el mismo error.

No hay estado entre ciclos: el orquestador crea un PgLogTail por ciclo y cada
ciclo relee la ventana.
"""
from collections.abc import Iterator

from sqlalchemy import text

LOG_FILES_SQL = "SELECT name, size FROM querylens.log_files() ORDER BY modification DESC"
READ_LOG_SQL = "SELECT querylens.read_log(:name, :start_offset, :max_bytes) AS chunk"

# Bytes que se leen por ciclo. Un plan JSON de auto_explain ocupa unos 2-6 KB,
# asi que entran varios cientos; un informe de abrazo mortal, menos de 1 KB.
LOG_WINDOW_BYTES = 2 * 1024 * 1024


class PgLogTail:
    def __init__(self):
        # archivos listados que todavia no se leyeron (None: falta listarlos)
        self._pending: list | None = None
        self._budget = 0
        # lineas completas de cada archivo ya leido, del mas nuevo al mas viejo
        self._read: list[list[bytes]] = []
        self._error: Exception | None = None

    def files(self, conn) -> Iterator[list[bytes]]:
        """Lineas completas de cada archivo, del mas nuevo al mas viejo; dentro
        de cada archivo, en el orden en que se escribieron. Quien corta la
        iteracion antes no gasta el resto del presupuesto."""
        index = 0
        while True:
            if index < len(self._read):
                yield self._read[index]
                index += 1
            elif not self._read_next(conn):
                return

    def _read_next(self, conn) -> bool:
        if self._error is not None:
            raise self._error
        try:
            if self._pending is None:
                self._pending = list(conn.execute(text(LOG_FILES_SQL)).mappings())
                self._budget = LOG_WINDOW_BYTES
            while self._pending and self._budget > 0:
                log_file = self._pending.pop(0)
                size = int(log_file["size"] or 0)
                if size <= 0:
                    continue
                length = min(size, self._budget)
                start_offset = size - length
                row = next(iter(conn.execute(
                    text(READ_LOG_SQL).bindparams(
                        name=log_file["name"], start_offset=start_offset, max_bytes=length
                    )
                ).mappings()), None)
                chunk = row["chunk"] if row is not None else None
                # NULL: el archivo roto entre listar y leer.
                if chunk is None:
                    continue
                self._budget -= length
                lines = bytes(chunk).split(b"\n")
                if start_offset > 0:
                    # La ventana empieza en medio de una linea (o de un
                    # caracter UTF-8): ese pedazo no es JSON valido.
                    lines = lines[1:]
                self._read.append(lines)
                return True
            return False
        except Exception as e:
            self._error = e
            raise
