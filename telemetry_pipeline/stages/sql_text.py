"""Lectura lexica minima de SQL: que parte del texto es codigo.

Las reglas de seguridad del EXPLAIN (una sola sentencia, solo lectura) miran
palabras clave y `;`. Ninguna de las dos puede mirar dentro de un string o de un
comentario: un `;` en un literal no separa sentencias, y un `'` en un comentario
no abre un string. `mask_sql` devuelve el texto con el contenido de strings,
identificadores citados y comentarios reemplazado, para que las reglas operen
solo sobre codigo.

No es un parser: si el texto no cierra un string o comentario (sample de MySQL
truncado), `mask_sql` devuelve None y quien llama decide de forma conservadora.
"""
import re

_IDENT_CHAR = re.compile(r"[A-Za-z0-9_$]")
# Postgres: $$ o $tag$ con tag que empieza por letra o _ ($1 es un parametro).
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def mask_sql(text: str, dialect: str = "postgres", comment: str = " ") -> str | None:
    """Codigo con strings -> '' , identificadores citados -> "" y comentarios ->
    `comment`. None si un string o comentario queda sin cerrar."""
    mysql = dialect == "mysql"
    out = []
    i, n = 0, len(text)
    in_exec_comment = False  # MySQL /*! ... */: el contenido es codigo

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        prev_ident = i > 0 and bool(_IDENT_CHAR.match(text[i - 1]))

        if in_exec_comment and ch == "*" and nxt == "/":
            in_exec_comment = False
            out.append(" ")
            i += 2
            continue

        # Comentario de bloque. Postgres los anida; MySQL no, y /*! es codigo.
        if ch == "/" and nxt == "*":
            if mysql and i + 2 < n and text[i + 2] == "!":
                in_exec_comment = True
                i += 3
                while i < n and text[i].isdigit():
                    i += 1
                out.append(" ")
                continue
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("*/", j):
                    depth -= 1
                    j += 2
                elif not mysql and text.startswith("/*", j):
                    depth += 1
                    j += 2
                else:
                    j += 1
            if depth:
                return None
            out.append(comment)
            i = j
            continue

        # Comentario de linea: "--" (MySQL exige espacio o control despues) y "#" en MySQL.
        if (ch == "-" and nxt == "-" and (not mysql or i + 2 >= n or text[i + 2] <= " ")) or (
            mysql and ch == "#"
        ):
            j = text.find("\n", i)
            out.append(comment)
            i = n if j == -1 else j
            continue

        if not mysql and ch == "$" and not prev_ident:
            tag = _DOLLAR_TAG.match(text, i)
            if tag:
                end = text.find(tag.group(0), tag.end())
                if end == -1:
                    return None
                out.append("''")
                i = end + len(tag.group(0))
                continue

        if ch in ("'", '"', "`"):
            # Backslash escapa en strings de MySQL y en E'...' de Postgres.
            backslash = ch != "`" and (
                mysql or (ch == "'" and i > 0 and text[i - 1] in "eE"
                          and not (i > 1 and _IDENT_CHAR.match(text[i - 2])))
            )
            j = i + 1
            while j < n:
                if backslash and text[j] == "\\":
                    j += 2
                    continue
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:  # '' / "" / `` duplicado
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                return None
            out.append("''" if ch == "'" else '""')
            i = j + 1
            continue

        out.append(ch)
        i += 1

    if in_exec_comment:
        return None
    return "".join(out)


def is_single_statement(query_text: str | None, dialect: str = "postgres") -> bool:
    """True si el texto es exactamente una sentencia.

    Un `;` dentro de strings, identificadores citados, dollar-quotes o
    comentarios no separa. Se tolera un `;` final; cualquier cosa despues de el
    (incluso un comentario) cuenta como otra sentencia: el driver de Postgres
    ejecuta multi-sentencia en un solo execute y no se arriesga.
    Texto con string/comentario sin cerrar -> False (no se explica).
    """
    if not query_text:
        return False
    code = mask_sql(query_text, dialect, comment="~")
    if code is None:
        return False
    code = code.rstrip()
    if code.endswith(";"):
        code = code[:-1]
    return ";" not in code


EXPLAINABLE_COMMANDS = ("SELECT", "WITH")

# Con el rol monitor de solo lectura, el EXPLAIN de estos casos falla por
# permisos aunque la sentencia empiece por SELECT/WITH:
#  - CTE que modifica datos: WITH d AS (DELETE ... RETURNING *) SELECT ...
#  - clausulas de bloqueo: FOR UPDATE / NO KEY UPDATE / SHARE / KEY SHARE,
#    LOCK IN SHARE MODE (PG y MySQL 8 exigen UPDATE/DELETE/LOCK TABLES).
_WRITE_OR_LOCK = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|REPLACE\s+INTO)\b"
    r"|\bFOR\s+(?:KEY\s+)?SHARE\b"
    r"|\bLOCK\s+IN\s+SHARE\s+MODE\b",
    re.IGNORECASE,
)
_LEADING_PARENS = re.compile(r"^[\s(]*")


def is_explainable_command(query_text: str | None, dialect: str = "postgres") -> bool:
    """SELECT/WITH de solo lectura: lo unico que el rol monitor puede EXPLAINar."""
    if not query_text:
        return False
    code = mask_sql(query_text, dialect)
    if code is None:
        return False
    head = _LEADING_PARENS.sub("", code, count=1).upper()
    if not head.startswith(EXPLAINABLE_COMMANDS):
        return False
    return _WRITE_OR_LOCK.search(code) is None
