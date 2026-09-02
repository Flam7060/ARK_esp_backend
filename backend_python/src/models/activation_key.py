"""ORM-модель `activation_key`. Гасится ровно 1 раз: сервисный слой берёт
строку `FOR UPDATE` и атомарно переводит issued -> redeemed — модель
только хранит состояние, блокировку/переход обеспечивает репозиторий."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import INTERVAL, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class ActivationKey(Base):
    __tablename__ = "activation_key"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, comment="SHA-256, НЕ argon2")
    duration: Mapped[timedelta] = mapped_column(INTERVAL, nullable=False)
    origin_code: Mapped[str] = mapped_column(
        Text, ForeignKey("activation_key_origin.code"), nullable=False, default="purchase", server_default="purchase"
    )
    status_code: Mapped[str] = mapped_column(
        Text, ForeignKey("activation_key_status.code"), nullable=False, default="issued", server_default="issued"
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=True, index=True
    )
    tg_user_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True, comment="кто купил = идентификатор продажи"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
