"""Репозиторий `dino_density` для агрегации из живого Redis-слоя
(services/dino_density_service.py).

В отличие от остальных ingestion-репозиториев здесь нет поиска "той же
сущности эвристикой": ключ строки полностью детерминирован (сервер, размер
ячейки, ячейка, трайб, окно), поэтому одна атомарная INSERT ... ON CONFLICT
вместо SELECT-и-реши. Это важно не только для скорости: агрегатор может
запуститься параллельно на двух процессах, и гонка "оба не нашли, оба
вставили" здесь исключена уникальным индексом, а не удачей.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from models.dino_density import DinoDensity


def upsert_cell(
    session: Session,
    *,
    server_id: uuid.UUID,
    tribe_id: uuid.UUID,
    cell_size_units: int,
    cell_x: int,
    cell_y: int,
    bucket_start: datetime,
    count: int,
    observed_at: datetime,
    reported_by_account_id: uuid.UUID | None,
) -> None:
    """Записывает замер в ячейку: `count_last` перетирается всегда,
    `count_max` растёт монотонно в пределах окна.

    GREATEST на стороне БД, а не max() в Python: между чтением и записью
    ячейку может обновить другой замер, и посчитанный заранее максимум
    затёр бы чужой более высокий пик. Здесь же старое значение и новое
    сравнивает сама СУБД внутри той же операции.
    """
    stmt = insert(DinoDensity).values(
        id=uuid.uuid4(),
        server_id=server_id,
        tribe_id=tribe_id,
        cell_size_units=cell_size_units,
        cell_x=cell_x,
        cell_y=cell_y,
        bucket_start=bucket_start,
        count_max=count,
        count_last=count,
        observed_at=observed_at,
        reported_by_account_id=reported_by_account_id,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_dino_density_cell",
        set_={
            "count_last": stmt.excluded.count_last,
            "count_max": func.greatest(DinoDensity.count_max, stmt.excluded.count_last),
            "observed_at": stmt.excluded.observed_at,
            "reported_by_account_id": stmt.excluded.reported_by_account_id,
        },
    )
    session.execute(stmt)
    session.commit()
