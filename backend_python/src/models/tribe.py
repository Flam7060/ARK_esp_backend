"""ORM-модель `tribe`. `updated_at` — аудит строки (когда воркер её
тронул), НЕ триггерится БД (`onupdate`) намеренно: значение проставляет
воркер вместе с `last_updated_by_account_id`, а не сам факт UPDATE —
иначе два поля разойдутся по смыслу."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Tribe(Base):
    __tablename__ = "tribe"
    __table_args__ = (
        Index("uq_tribe_server_name", "server_id", "name", unique=True),
        Index("ix_tribe_last_updated_by_account_id", "last_updated_by_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("server.id"), nullable=False)
    ark_tribe_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="если есть сейв; иначе по имени"
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="аудит: когда воркер тронул строку"
    )
    last_updated_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=True, comment="АВТОР последнего апдейта"
    )

    server: Mapped["Server"] = relationship()
    players: Mapped[list["Player"]] = relationship(back_populates="tribe")
    structures: Mapped[list["ArkStructure"]] = relationship(back_populates="tribe")
    tamed_dinos: Mapped[list["TamedDino"]] = relationship(back_populates="tribe")
