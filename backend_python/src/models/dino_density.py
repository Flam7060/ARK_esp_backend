"""ORM-модель `dino_density` — тепловая карта ручных дино вместо строки на
каждое животное.

Зачем отдельно от `tamed_dino`: у дино нет настоящей идентичности (это
прямо признано в докстринге `tamed_dino_repo`), поэтому строка-на-животное
даёт объём без информации. Ценность не в "вот этот конкретный рекс", а в
"здесь сгущение питомцев такого-то трайба" — то есть база, масса, чья она.
Дикие сюда не попадают вовсе: они живут только в живом Redis-слое для ESP и
в Postgres не пишутся ни в каком виде (см. hub.maybeStream на Go-стороне).

Ячейка адресуется целыми индексами (`cell_x`, `cell_y`), мировые координаты
из них выводятся умножением на `cell_size_units` — поэтому не хранятся.
Имя трайба тоже не хранится: оно зависит от `tribe_id`, а не от ключа
строки, и дублировать его здесь значило бы завести транзитивную зависимость
и рассинхрон при переименовании племени.

Питомцы, чей трайб не удалось определить, не пишутся: карта отвечает на
вопрос "чья масса", и строка без владельца на него не отвечает — см.
комментарий у `tribe_id`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class DinoDensity(Base):
    __tablename__ = "dino_density"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("server.id"), nullable=False
    )
    tribe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tribe.id"),
        nullable=False,
        comment="Обязателен: строка без владельца не несёт смысла (карта отвечает 'чья масса'), "
        "а NULL в составе уникального ключа ломал бы ON CONFLICT — в Postgres NULL != NULL, "
        "и такие ячейки копились бы дубликатами на каждый замер",
    )

    cell_size_units: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Размер ячейки в игровых юнитах. В ключе, а не константой в коде: "
        "поменяется — старые строки останутся интерпретируемыми и явно несравнимыми с новыми",
    )
    cell_x: Mapped[int] = mapped_column(Integer, nullable=False, comment="floor(x / cell_size_units)")
    cell_y: Mapped[int] = mapped_column(Integer, nullable=False, comment="floor(y / cell_size_units)")

    bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Начало временного окна агрегации"
    )

    count_max: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Пик за окно — какая масса там вообще стояла"
    )
    count_last: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Последний замер в окне. Отдельно от count_max: max без last не отличает "
        "'было и разошлось' от 'стоит сейчас', last без max теряет пик между заходами скаута",
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="Когда ячейку последний раз обновляли"
    )
    reported_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "server_id",
            "cell_size_units",
            "cell_x",
            "cell_y",
            "tribe_id",
            "bucket_start",
            name="uq_dino_density_cell",
        ),
        Index("ix_dino_density_server_bucket", "server_id", "bucket_start"),
    )
