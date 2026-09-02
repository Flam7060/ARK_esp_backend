"""УСТАРЕЛО: читает models/structure_legacy.py (MVP-заглушку) через
structure_repo. Не удалено — используется routers/v1/tribes.py.

Сервис чтения `structure` — курсор-строка <-> (datetime, UUID) поверх
repositories/structure_repo.list_page. Симметрично services/user_service.py
list_users; тут нет доменных исключений вроде ConflictError — читать
постройки нечему конфликтовать, чтение всегда "успех или пустая страница".
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from core.pagination import decode_cursor, encode_cursor
from models.structure_legacy import Structure
from repositories import structure_repo

DEFAULT_LIMIT = 200
MAX_LIMIT = 500


def list_structures(
    session: Session, tribe_id: UUID, map_id: str | None, cursor: str | None, limit: int
) -> tuple[list[Structure], str | None]:
    limit = max(1, min(limit, MAX_LIMIT))
    after = decode_cursor(cursor) if cursor else None

    rows = structure_repo.list_page(session, tribe_id, map_id, after, limit + 1)

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.last_seen_at, last.id)

    return rows, next_cursor
