from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connection_tester import test_connection as run_connection_test
from app.database import get_db
from app.models import RegisteredDatabase
from app.schemas import (
    DatabaseCreateRequest,
    DatabaseRegisteredResponse,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.security import decrypt_secret, encrypt_secret, generate_database_identifier

router = APIRouter(prefix="/databases", tags=["databases"])

MAX_IDENTIFIER_ATTEMPTS = 5


@router.post("/test-connection", response_model=TestConnectionResponse)
def test_connection(payload: TestConnectionRequest) -> TestConnectionResponse:
    return run_connection_test(payload)


@router.post(
    "",
    response_model=DatabaseRegisteredResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_database(
    payload: DatabaseCreateRequest, db: Session = Depends(get_db)
) -> DatabaseRegisteredResponse:
    # database_identifier es aleatorio (8 hex = 32 bits) y puede chocar con uno
    # ya existente; eso no es un error del cliente, asi que reintentamos con un
    # identificador nuevo en vez de devolver un 409.
    for _ in range(MAX_IDENTIFIER_ATTEMPTS):
        record = RegisteredDatabase(
            database_identifier=generate_database_identifier(),
            connection_name=payload.connection_name,
            engine=payload.engine,
            host=encrypt_secret(payload.host),
            port=encrypt_secret(str(payload.port)),
            db_user=encrypt_secret(payload.username),
            encrypted_password=encrypt_secret(payload.password),
            database_name=payload.database_name,
        )

        db.add(record)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue

        db.refresh(record)
        return DatabaseRegisteredResponse(
            id=record.id,
            database_identifier=record.database_identifier,
            connection_name=record.connection_name,
            engine=record.engine,
            host=decrypt_secret(record.host),
            port=int(decrypt_secret(record.port)),
            database_name=record.database_name,
            created_at=record.created_at,
        )

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="No fue posible generar un identificador único para la base de datos",
    )
