import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_fernet = Fernet(settings.auth_encryption_key.encode())


def encrypt_secret(plain_text: str) -> str:
    return _fernet.encrypt(plain_text.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("No fue posible descifrar la credencial almacenada") from exc


def generate_database_identifier() -> str:
    return f"db_{uuid.uuid4().hex[:8]}"


def compute_connection_fingerprint(
    *, engine: str, host: str, port: int, username: str, database_name: str
) -> str:
    """Hash determinístico de los datos que identifican una conexión real.

    host/port/username se cifran con Fernet (no determinístico), así que no se
    puede usar un UNIQUE de Postgres sobre esas columnas para detectar
    duplicados. Este hash normaliza y firma (HMAC) esos mismos campos en texto
    plano antes de cifrarlos, y sí se puede indexar como UNIQUE.
    """

    normalized = "|".join(
        [
            engine.strip().lower(),
            host.strip().lower(),
            str(port).strip(),
            username.strip().lower(),
            database_name.strip().lower(),
        ]
    )
    return hmac.new(
        settings.auth_encryption_key.encode(), normalized.encode(), hashlib.sha256
    ).hexdigest()


def create_access_token(database_identifier: str) -> str:
    """Access token (JWT) de la sesión de monitoreo de una base de datos.

    `sub` es el database_identifier; la API REST lo usa para consultar los
    diagnósticos de esa base de datos. Se firma con JWT_SECRET_KEY, que la API
    REST debe compartir para poder verificarlo.
    """

    now = datetime.now(timezone.utc)
    claims = {
        "sub": database_identifier,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
