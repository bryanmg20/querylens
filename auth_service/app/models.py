import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RegisteredDatabase(Base):
    __tablename__ = "registered_databases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    database_identifier: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    connection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    engine: Mapped[str] = mapped_column(String(50), nullable=False, default="postgresql")
    # host, port y db_user se guardan cifrados (Fernet, ver app/security.py),
    # por eso son Text en vez de String/Integer: el texto cifrado es mas largo
    # que el valor original y no es un numero.
    host: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[str] = mapped_column(Text, nullable=False)
    db_user: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
