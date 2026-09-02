"""ORM-модель `tribe_log` — APPEND-ONLY: никогда UPDATE/DELETE, поэтому
здесь нет `updated_at` (событие неизменно, есть только автор — кто прислал
лог). `raw_text` хранит сырую строку всегда, распарсенные поля — сверху,
best-effort: нераспарсенное никогда не теряется."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class TribeLog(Base):
    __tablename__ = "tribe_log"
    __table_args__ = (Index("ix_tribe_log_server_occurred_at", "server_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("server.id"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    game_day: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="ARK Day N")

    event_type_code: Mapped[str] = mapped_column(
        Text, ForeignKey("log_event_type.code"), nullable=False, index=True
    )

    actor_tribe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tribe.id"), nullable=True, index=True
    )
    target_tribe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tribe.id"), nullable=True, index=True
    )

    actor_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    species_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("species.id"), nullable=True)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False, comment="СЫРАЯ строка — нераспарсенное не теряется")

    reported_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=True, index=True, comment="АВТОР (кто прислал лог)"
    )
