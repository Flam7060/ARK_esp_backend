"""УСТАРЕЛО: держится на models/structure_legacy.py (MVP-заглушке), замена —
models/ark_structure.py. Не удалено — используется structure_flush.py
и services/structure_query_service.py.

Репозиторий `structure` — только persistence: upsert/select/mark-stale
через Session, без парсинга Redis-строк и без HTTP-пагинации (это
services/structure_flush_service.py и services/structure_query_service.py
соответственно). Симметрично repositories/user_repo.py.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.structure_legacy import Structure


def upsert(
    session: Session,
    *,
    tribe_id: str,
    map_id: str,
    struct_key: str,
    first_seen_at: datetime,
    last_seen_at: datetime,
    class_: str,
    kind_hint: str | None,
    x: float | None,
    y: float | None,
    z: float | None,
    item_count: int | None,
    has_item_count: bool,
    ammo: float | None,
    range_: float | None,
    enabled: bool | None,
    powered: bool | None,
) -> Structure:
    """UC-1 шаг 4: если постройка уже известна (tribe_id, map_id, struct_key)
    — обновляет её, first_seen_at не трогается; иначе создаёт новую."""
    existing = session.execute(
        select(Structure).where(
            Structure.tribe_id == tribe_id,
            Structure.map_id == map_id,
            Structure.struct_key == struct_key,
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = Structure(
            tribe_id=tribe_id, map_id=map_id, struct_key=struct_key, first_seen_at=first_seen_at
        )
        session.add(existing)

    existing.class_ = class_
    existing.kind_hint = kind_hint
    existing.x, existing.y, existing.z = x, y, z
    existing.item_count = item_count
    existing.has_item_count = has_item_count
    existing.ammo = ammo
    existing.range = range_
    existing.enabled = enabled
    existing.powered = powered
    existing.last_seen_at = last_seen_at
    existing.status = "active"

    session.commit()
    return existing


def list_page(
    session: Session,
    tribe_id: UUID,
    map_id: str | None,
    after: tuple[datetime, UUID] | None,
    limit: int,
) -> list[Structure]:
    stmt = select(Structure).where(Structure.tribe_id == tribe_id)
    if map_id:
        stmt = stmt.where(Structure.map_id == map_id)

    if after is not None:
        last_seen_at, row_id = after
        # Keyset, не OFFSET (§6): растущая таблица не даёт OFFSET-у
        # согласованных страниц. Тай-брейк по id — last_seen_at сама по
        # себе не уникальна между разными постройками одного снимка.
        stmt = stmt.where(
            (Structure.last_seen_at < last_seen_at)
            | ((Structure.last_seen_at == last_seen_at) & (Structure.id < row_id))
        )

    stmt = stmt.order_by(Structure.last_seen_at.desc(), Structure.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def mark_stale(session: Session, cutoff: datetime) -> int:
    """UC-1 шаг 5: постройки, не тронутые дольше stale_after — статус
    possibly_demolished, а не автоудаление (снос неотличим от «клиент был
    выключен весь день»)."""
    rows = list(
        session.execute(
            select(Structure).where(Structure.status == "active", Structure.last_seen_at < cutoff)
        ).scalars()
    )
    for row in rows:
        row.status = "possibly_demolished"
    session.commit()
    return len(rows)
