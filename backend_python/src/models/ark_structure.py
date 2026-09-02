"""ORM-модели структуры (§ARK/СОСТОЯНИЕ) — супертип `ark_structure` +
подтипы 1:1 `ark_structure_turret`/`ark_structure_generator`, разделяющие
PK/FK `structure_id` (joined-extension, а не SQLAlchemy joined-table
inheritance: принадлежность к подтипу не эксклюзивна и не решается
дискриминатором на супертипе — её решает
`structure_class.is_turret`/`is_powered_type`, волатильные пачки живут
отдельно от редко меняющихся полей супертипа).

Имя таблицы/класса — `ark_structure`/`ArkStructure`, не голое
`structure`/`Structure` из DBML: в этом бэкенде уже есть
`models/structure_legacy.py::Structure` (таблица `structure`, MVP-телеметрия,
см. её докстринг) — префикс `ark_` снимает конфликт физически, а не
процедурно (не нужно помнить "не подключать вместе со старой моделью").
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, REAL, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class ArkStructure(Base):
    __tablename__ = "ark_structure"
    __table_args__ = (
        Index("uq_ark_structure_server_object_hash", "server_id", "object_hash", unique=True),
        Index("ix_ark_structure_tribe_id", "tribe_id"),
        Index("ix_ark_structure_decay_remaining_seconds", "decay_remaining_seconds"),
        Index("ix_ark_structure_last_updated_by_account_id", "last_updated_by_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("server.id"), nullable=False)

    object_hash: Mapped[str] = mapped_column(
        Text, nullable=False, comment="hash(class + КВАНТ. x/y/z до snap-грида)"
    )

    class_code: Mapped[str] = mapped_column(Text, ForeignKey("structure_class.code"), nullable=False)
    tribe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tribe.id"), nullable=True, comment="NULL допустим"
    )

    x: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    y: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    z: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)

    health: Mapped[float | None] = mapped_column(REAL, nullable=True)

    decay_remaining_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="ФАКТИЧЕСКОЕ оставшееся время, полученное из игры; НЕ вычисляется"
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="первое наблюдение"
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="последнее наблюдение"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="аудит: когда воркер тронул строку"
    )

    last_updated_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=True, comment="АВТОР последнего апдейта"
    )

    tribe: Mapped["Tribe | None"] = relationship(back_populates="structures")
    # ondelete=CASCADE — допущение: строка-расширение 1:1 бессмысленна без
    # родителя, в DBML ON DELETE не задан явно.
    turret: Mapped["ArkStructureTurret | None"] = relationship(
        back_populates="structure", uselist=False, cascade="all, delete-orphan"
    )
    generator: Mapped["ArkStructureGenerator | None"] = relationship(
        back_populates="structure", uselist=False, cascade="all, delete-orphan"
    )


class ArkStructureTurret(Base):
    """ПОДТИП 1:1. Только турели (structure_class.is_turret = true)."""

    __tablename__ = "ark_structure_turret"

    structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ark_structure.id", ondelete="CASCADE"), primary_key=True
    )
    ammo_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    range: Mapped[float | None] = mapped_column(REAL, nullable=True, comment="радиус — карта покрытия")
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mode: Mapped[str | None] = mapped_column(Text, nullable=True, comment="players/dinos/all/survivors")

    structure: Mapped["ArkStructure"] = relationship(back_populates="turret")


class ArkStructureGenerator(Base):
    """ПОДТИП 1:1. Обесточенные = WHERE is_powered = false."""

    __tablename__ = "ark_structure_generator"

    structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ark_structure.id", ondelete="CASCADE"), primary_key=True
    )
    is_powered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fuel_amount: Mapped[float | None] = mapped_column(REAL, nullable=True)
    power_range: Mapped[float | None] = mapped_column(REAL, nullable=True)

    structure: Mapped["ArkStructure"] = relationship(back_populates="generator")
