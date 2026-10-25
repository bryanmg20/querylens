"""Genera un .env valido para docker-compose.yml con secretos aleatorios.

    python3 scripts/gen_env.py > .env

Escribe las variables obligatorias y el uid/gid del usuario; las demas
opcionales (con valor por defecto en docker-compose.yml) estan documentadas
en .env.example. El CI usa
este mismo script para su .env desechable.

Cambiar AUTH_ENCRYPTION_KEY con un volumen pgdata existente deja ilegibles
las contrasenas de las bases ya registradas: regenerar el .env solo con un
volumen nuevo, o conservar esa clave.
"""

import base64
import os
import secrets

print("QUERYLENS_USER=ql_user")
print("QUERYLENS_DB=ql_demo")
print(f"QUERYLENS_PASSWORD={secrets.token_hex(16)}")
# Fernet: 32 bytes en base64 url-safe.
print(f"AUTH_ENCRYPTION_KEY={base64.urlsafe_b64encode(os.urandom(32)).decode()}")
print(f"JWT_SECRET_KEY={secrets.token_urlsafe(48)}")
for role in ("AUTH", "API", "PIPELINE", "DETECTOR"):
    print(f"QL_{role}_DB_PASSWORD={secrets.token_hex(16)}")
# uid/gid de quien genera el .env, para el servicio tests (default 1000). En
# Windows no existen y Docker Desktop no los necesita.
if hasattr(os, "getuid"):
    print(f"HOST_UID={os.getuid()}")
    print(f"HOST_GID={os.getgid()}")
