from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
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
    # Registrar abre la sesión y marca la base de datos como disponible. El
    # monitoreo no se toca: una base nueva nace apagada (valor por defecto de la
    # columna) y una que ya existía conserva su estado, que solo cambian el usuario
    # (PATCH /databases/monitoring) y su ventana de recolección.
    if not record.is_active:
        record.is_active = True
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
    """Pausa (enabled=false) o inicia/reanuda (enabled=true) el monitoreo de la
    base de datos del token. La disponibilidad (is_active) sigue siendo
    responsabilidad del chequeo automático.

    new_session=true, solo al iniciar o reanudar, empieza una sesión de
    detección nueva: la página de diagnósticos pasa a contar solo lo detectado
    desde ahora (no se borra nada). La sesión solo cambia cuando el monitoreo
    arranca: con el monitoreo encendido, el detector todavía procesa snapshots
    tomados antes del corte. La primera vez que se inicia, la página la pide
    siempre."""

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
    if payload.new_session and not payload.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Una sesión de detección nueva solo se empieza al iniciar o reanudar el monitoreo",
        )
    if payload.new_session and record.monitoring_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El monitoreo ya está activo: páusalo para empezar una sesión de detección nueva",
        )

    if payload.new_session:
        # Hora del servidor de la base, la misma con la que el detector anota las detecciones.
        record.detection_session_started_at = func.now()
    record.monitoring_enabled = payload.enabled
    db.commit()
    db.refresh(record)
    return MonitoringResponse(
        database_identifier=record.database_identifier,
        monitoring_enabled=record.monitoring_enabled,
        detection_session_started_at=record.detection_session_started_at,
    )
