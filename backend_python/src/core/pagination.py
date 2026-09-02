"""Курсорная пагинация — общий формат для всех сущностей (§6
telemetry-api-v1.md: "курсорная (?cursor=...&limit=...), не offset/limit").

Раньше encode/decode были продублированы дословно в user- и
structure-сервисах — здесь один раз, все сущности берут отсюда.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from uuid import UUID


class InvalidCursorError(Exception):
    """Курсор нечитаем — не наш формат или битый base64."""


def encode_cursor(sort_at: datetime, row_id: UUID) -> str:
    raw = f"{sort_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_raw, id_raw = raw.split("|", 1)
        return datetime.fromisoformat(ts_raw), UUID(id_raw)
    except (ValueError, binascii.Error) as exc:
        raise InvalidCursorError(str(exc)) from exc
