"""Топология: `cluster` (группа серверов с переносом, NULL у одиночных)
-> `server` -> набор наблюдаемых сущностей. `person` — наблюдаемый игрок
платформы, НЕ наш сервисный `account`."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Cluster(Base):
    __tablename__ = "cluster"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    servers: Mapped[list["Server"]] = relationship(back_populates="cluster")


class Server(Base):
    __tablename__ = "server"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cluster.id"), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    port: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Игровой порт отдельно от ip_address: INET не принимает 'ip:port' — "
        "ark_relay's server_ip (DTO-sharing plan §4) несёт оба сразу, разбирается на границе.",
    )
    map_code: Mapped[str] = mapped_column(Text, ForeignKey("game_map.code"), nullable=False)

    cluster: Mapped["Cluster | None"] = relationship(back_populates="servers")


class Person(Base):
    __tablename__ = "person"

    platform_id: Mapped[str] = mapped_column(Text, primary_key=True, comment="НАБЛЮДАЕМЫЙ игрок (не наш аккаунт!)")
    platform_name: Mapped[str | None] = mapped_column(Text, nullable=True)
