"""ORM-модель `tamed_dino` — мувер: у динозавра нет стабильного игрового
Addr между кадрами наблюдения, идентичность строится сигналом
`object_hash` (species+gender+цвет регионов, растущие поля — level, имя —
в ключ не идут) и подтверждается близостью координат между кадрами на
уровне сервисного слоя, не здесь."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class TamedDino(Base):
    __tablename__ = "tamed_dino"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("server.id"), nullable=False, index=True
    )
    tribe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tribe.id"),
        nullable=True,
        index=True,
        comment="NULL = анклейм. В object_hash НЕ идёт",
    )
    species_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("species.id"), nullable=False, index=True
    )

    gender: Mapped[str | None] = mapped_column(Text, nullable=True, comment="male/female")

    color_0: Mapped[int | None] = mapped_column(SmallInteger, nullable=True, comment="регионы 0..5 — стабильны у взрослого")
    color_1: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    color_2: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    color_3: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    color_4: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    color_5: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    object_hash: Mapped[str | None] = mapped_column(
        Text, nullable=True, index=True, comment="hash(species + gender + color_0..5). СИГНАЛ, не unique"
    )

    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="растёт — не в ключ")

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

    tribe: Mapped["Tribe | None"] = relationship(back_populates="tamed_dinos")
