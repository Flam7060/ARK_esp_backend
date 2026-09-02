"""ORM-модель `player`. `character_name` — меняемый игровой ник, не
ключ идентичности (ключ — (server_id, platform_id)); `account_id` NULL
означает игрока без аккаунта сервиса, `tribe_id` NULL — бестрайбового."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Player(Base):
    __tablename__ = "player"
    __table_args__ = (Index("uq_player_server_platform", "server_id", "platform_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("server.id"), nullable=False)
    platform_id: Mapped[str] = mapped_column(Text, ForeignKey("person.platform_id"), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=True, index=True
    )
    tribe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tribe.id"), nullable=True, index=True
    )
    character_name: Mapped[str | None] = mapped_column(Text, nullable=True, comment="ник — меняется, НЕ ключ")
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    x: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    y: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    z: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="когда наблюдался в игре"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="аудит: когда воркер тронул строку"
    )
    last_updated_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=True, index=True, comment="АВТОР последнего апдейта"
    )

    tribe: Mapped["Tribe | None"] = relationship(back_populates="players")
