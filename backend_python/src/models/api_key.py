"""ORM-модели `api_key` (плейнтекст ключа виден клиенту 1 раз, хранится
только SHA-256) и `api_key_scope` — состав прав ключа, чистая M:N-строка
без суррогатного id (PK — сама пара)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class ApiKey(Base):
    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, comment="SHA-256; плейнтекст 1 раз")
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    last4: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[str] = mapped_column(
        Text, ForeignKey("api_key_status.code"), nullable=False, default="active", server_default="active"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    scopes: Mapped[list["ApiKeyScope"]] = relationship(back_populates="api_key", cascade="all, delete-orphan")


class ApiKeyScope(Base):
    __tablename__ = "api_key_scope"

    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("api_key.id"), primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)

    api_key: Mapped["ApiKey"] = relationship(back_populates="scopes")
