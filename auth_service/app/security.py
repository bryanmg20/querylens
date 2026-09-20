import uuid

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
