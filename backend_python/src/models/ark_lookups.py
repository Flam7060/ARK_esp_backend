"""Справочники ARK-домена. `game_map`/`species`/`structure_class` не
переиспользуют `CodeLookup` — форма (доп. поля, либо `name` вместо
`label`, либо suid вместо code-PK) у каждого своя. `log_event_type`
— чистый code/label, переиспользует миксин."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base
from models.mixins import CodeLookup


class GameMap(Base):
    """ШАБЛОН карты. seed: theisland, ragnarok ..."""

    __tablename__ = "game_map"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)


class Species(Base):
    __tablename__ = "species"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    blueprint_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)


class StructureClass(Base):
    __tablename__ = "structure_class"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False, comment="turret/generator/foundation/wall/...")
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_decay_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="эталонное время жизни класса без рефреша"
    )
    is_turret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_powered_type: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class LogEventType(CodeLookup):
    """seed: tame, kill, structure_destroyed, member_joined, member_left, claimed, demolished"""

    __tablename__ = "log_event_type"
