from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connection_tester import test_connection as run_connection_test
from app.database import get_db
from app.models import RegisteredDatabase
from app.schemas import (
    DatabaseCreateRequest,
    DatabaseRegisteredResponse,
    DatabaseSessionResponse,
    MonitoringResponse,
    MonitoringUpdateRequest,
    TestConnectionRequest,
    TestConnectionResponse,
)
from app.security import (
    compute_connection_fingerprint,
    create_access_token,
    decrypt_secret,
    encrypt_secret,
    generate_database_identifier,
    get_database_identifier,
)

router = APIRouter(prefix="/databases", tags=["databases"])

MAX_IDENTIFIER_ATTEMPTS = 5


@router.post("/test-connection", response_model=TestConnectionResponse)
def test_connection(payload: TestConnectionRequest) -> TestConnectionResponse:
    return run_connection_test(payload)


def _to_response(record: RegisteredDatabase) -> DatabaseRegisteredResponse:
    return DatabaseRegisteredResponse(
        id=record.id,
        database_identifier=record.database_identifier,
        connection_name=record.connection_name,
        engine=record.engine,
        host=decrypt_secret(record.host),
        port=int(decrypt_secret(record.port)),
        database_name=record.database_name,
        is_active=record.is_active,
        monitoring_enabled=record.monitoring_enabled,
        created_at=record.created_at,
    )


def _start_session(db: Session, record: RegisteredDatabase) -> DatabaseSessionResponse:
    # Iniciar sesión marca la base de datos como disponible y vuelve a habilitar
    # el monitoreo si el usuario lo había pausado. Se queda así aunque cierre la
    # página: el monitoreo continúa.
    if not record.is_active or not record.monitoring_enabled:
        record.is_active = True
        record.monitoring_enabled = True
        db.commit()
        db.refresh(record)
    return DatabaseSessionResponse(
        **_to_response(record).model_dump(),
        access_token=create_access_token(record.database_identifier),
    )


def _find_by_fingerprint(db: Session, fingerprint: str) -> RegisteredDatabase | None:
    return (
        db.query(RegisteredDatabase)
        .filter(RegisteredDatabase.connection_fingerprint == fingerprint)
        .first()
    )


@router.post(
    "",
    response_model=DatabaseSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_database(
    payload: DatabaseCreateRequest, response: Response, db: Session = Depends(get_db)
) -> DatabaseSessionResponse:
    # engine+host+port+db_user+database_name identifican una conexión real;
    # si ya existe, no creamos otra fila: actualizamos esa misma (por si la
    # contraseña o el nombre cambiaron) y la devolvemos con 200 en vez de 201.
    fingerprint = compute_connection_fingerprint(
        engine=payload.engine,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        database_name=payload.database_name,
    )

    existing = _find_by_fingerprint(db, fingerprint)
    if existing is not None:
        existing.connection_name = payload.connection_name
        existing.host = encrypt_secret(payload.host)
        existing.port = encrypt_secret(str(payload.port))
        existing.db_user = encrypt_secret(payload.username)
        existing.encrypted_password = encrypt_secret(payload.password)
        db.commit()
        db.refresh(existing)
        response.status_code = status.HTTP_200_OK
        return _start_session(db, existing)

    # database_identifier es aleatorio (8 hex = 32 bits) y puede chocar con uno
    # ya existente; eso no es un error del cliente, asi que reintentamos con un
    # identificador nuevo en vez de devolver un 409.
    for _ in range(MAX_IDENTIFIER_ATTEMPTS):
        record = RegisteredDatabase(
            database_identifier=generate_database_identifier(),
            connection_fingerprint=fingerprint,
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
            # Pudo chocar el database_identifier (reintentamos) o el
            # connection_fingerprint por una inserción concurrente de la
            # misma conexión (la devolvemos como si ya existiera).
            existing = _find_by_fingerprint(db, fingerprint)
            if existing is not None:
                response.status_code = status.HTTP_200_OK
                return _start_session(db, existing)
            continue

        db.refresh(record)
        return _start_session(db, record)

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="No fue posible generar un identificador único para la base de datos",
    )


@router.patch("/monitoring", response_model=MonitoringResponse)
def set_monitoring(
    payload: MonitoringUpdateRequest,
    database_identifier: str = Depends(get_database_identifier),
    db: Session = Depends(get_db),
) -> MonitoringResponse:
    """Pausa (enabled=false) o reanuda (enabled=true) el monitoreo de la base
    de datos del token. Solo cambia monitoring_enabled: la disponibilidad
    (is_active) sigue siendo responsabilidad del chequeo automático."""

    record = (
        db.query(RegisteredDatabase)
        .filter(RegisteredDatabase.database_identifier == database_identifier)
        .first()
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="La base de datos del token ya no está registrada",
        )

    record.monitoring_enabled = payload.enabled
    db.commit()
    return MonitoringResponse(
        database_identifier=record.database_identifier,
        monitoring_enabled=record.monitoring_enabled,
    )
