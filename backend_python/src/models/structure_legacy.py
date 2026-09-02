"""УСТАРЕЛО: MVP-заглушка, замена — models/ark_structure.py::ArkStructure
(полная схема; названа не `Structure`/`structure`, а `ArkStructure`/
`ark_structure` именно чтобы не конфликтовать с этим файлом). Эта модель
не удалена, т.к. на неё всё ещё завязаны structure_repo.py,
structure_query_service.py, structure_flush.py и routers/v1/tribes.py —
их вывод из эксплуатации отдельным шагом.

ORM-модель `structure` — единственная durable-таблица телеметрии,
см. docs/telemetry-api-v1.md §8.3. Схема — прямая калька SQL из документа;
не меняй имена колонок без синхронной правки Redis-ключа §8.2
(`struct_key` там и здесь — один и тот же ключ, апсерт идёт по нему
напрямую, без поиска соответствия).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Structure(Base):
    __tablename__ = "structure"
    __table_args__ = (
        UniqueConstraint("tribe_id", "map_id", "struct_key", name="uq_structure_tribe_map_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tribe_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    map_id: Mapped[str] = mapped_column(String, nullable=False)
    # Тот же составной ключ (класс + округлённая координата), что и в
    # Redis-кэше §8.2 — FR-5: у построек нет стабильного Addr между
    # перезапусками игры, идентичность строится на этом ключе.
    struct_key: Mapped[str] = mapped_column(String, nullable=False)

    class_: Mapped[str] = mapped_column("class", String, nullable=False)
    kind_hint: Mapped[str | None] = mapped_column(String, nullable=True)

    x: Mapped[float | None] = mapped_column(Float, nullable=True)
    y: Mapped[float | None] = mapped_column(Float, nullable=True)
    z: Mapped[float | None] = mapped_column(Float, nullable=True)

    item_count: Mapped[int | None] = mapped_column(nullable=True)
    has_item_count: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    ammo: Mapped[float | None] = mapped_column(Float, nullable=True)
    range: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    powered: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # active | possibly_demolished — см. UC-1, шаг 5: снос неотличим от
    # «клиент был выключен весь день», поэтому это ручной статус, не
    # автоудаление.
    status: Mapped[str] = mapped_column(String, nullable=False, default="active", server_default="active")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
