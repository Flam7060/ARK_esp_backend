"""ORM-модели устройства-отпечатка: `fingerprint` (агрегат по account) +
`component_type` (справочник компонентов с весом для скоринга совпадения)
+ `fingerprint_component` (значения компонентов, M:N по факту 1:N с
уникальностью пары)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Fingerprint(Base):
    __tablename__ = "fingerprint"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=False, index=True
    )
    composite_hash: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    components: Mapped[list["FingerprintComponent"]] = relationship(
        back_populates="fingerprint", cascade="all, delete-orphan"
    )


class ComponentType(Base):
    __tablename__ = "component_type"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True, comment="cpu_id, motherboard, mac, ip ...")
    weight: Mapped[int] = mapped_column(Integer, nullable=False)


class FingerprintComponent(Base):
    __tablename__ = "fingerprint_component"
    __table_args__ = (
        UniqueConstraint("fingerprint_id", "component_type_id", name="uq_fingerprint_component_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fingerprint.id"), nullable=False
    )
    component_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("component_type.id"), nullable=False
    )
    value_hash: Mapped[str] = mapped_column(Text, nullable=False)

    fingerprint: Mapped["Fingerprint"] = relationship(back_populates="components")
