"""Orden de los hallazgos por impacto (SegundoInforme 6.2).

Los hallazgos van de mayor a menor impacto. Los empates se resuelven por
severidad (alta, media, baja) y, despues, por el codigo de la patologia. La
severidad dice cuanto se aparta la evidencia del umbral de su regla: desempata,
no ordena.

El orden depende solo de los hallazgos: la misma entrada da siempre el mismo
orden, llegue en el orden que llegue. Para que no quede ningun empate sin
resolver, el ultimo criterio es la clave del problema (storage.finding_key).

La API de diagnosticos y el reporte de la CLI ordenan los problemas con la
misma regla sobre el impacto acumulado de sus detecciones: si cambia aca,
cambiar tambien el ORDER BY de api_rest/app/routers/diagnostics.py y de
cli/share/report.sql.
"""
from catalog import code_of
from models import Hallazgo
from storage import finding_key

# menor = primero
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_UNKNOWN_SEVERITY = len(SEVERITY_RANK)

# Una regla fuera del catalogo va despues de todas las del catalogo ("~" es
# mayor que cualquier codigo; en SQL, NULLS LAST)
_NO_CODE = "~"


def priority_key(finding: Hallazgo) -> tuple:
    """Clave de orden de un hallazgo: el menor va primero.

    Un impacto sin medir (None, hoy los abrazos mortales) cuenta como 0: no se
    le inventa un valor, y entre los que empatan en 0 deciden la severidad y el
    codigo."""
    return (
        -(finding.impacto_ms or 0.0),
        SEVERITY_RANK.get(finding.severidad, _UNKNOWN_SEVERITY),
        code_of(finding.antipatron) or _NO_CODE,
        finding_key(finding),
    )


def prioritize(findings: list[Hallazgo]) -> list[Hallazgo]:
    """Los mismos hallazgos, ordenados por impacto. No modifica la lista."""
    return sorted(findings, key=priority_key)
