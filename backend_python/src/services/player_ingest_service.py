"""Сервис ingestion `player` из Redis Stream — первый конкретный consumer
поверх core/redis_stream.py. Контракт сообщения (поля `XADD`), с апстрима
(`ark_relay`'s internal/streamproducer.PlayerFields, actual producer since
CategoryPlayer stopped being excluded from streamproducer.StreamNameFor):

    server_ip               (str, обязателен)  — "ip:port", резолвится в
                                                  models.topology.Server
                                                  через тот же
                                                  server_repo.get_or_create_server_by_ip,
                                                  что structure/dino
                                                  ingestion (не готовый
                                                  UUID -- Go знает только
                                                  строку подключения)
    platform_id              (str, обязателен)  — настоящий SteamID64,
                                                   стрингифицированный, когда
                                                   Go-продюсер его знает;
                                                   иначе (владелец отключился
                                                   в момент захвата на
                                                   клиенте) — фоллбек на ARK
                                                   linked_player_data_id (см.
                                                   streamproducer.PlayerFields'
                                                   собственный doc-комментарий
                                                   на Go-стороне). Эта модель
                                                   не различает две формы —
                                                   для неё это просто строка-
                                                   идентификатор.
    character_name          (str, опционален)  — текущий игровой ник
    level                   (int, опционален)  — Go-продюсер это поле пока не шлёт
    x, y, z                 (float, опциональны)
    observed_at             (ISO 8601, опционален — по умолчанию время приёма)
    reported_by_account_id  (UUID, опционален)  — чей клиент это увидел

Поле, которого нет в сообщении, НЕ затирает сохранённое значение (частичное
обновление — см. repositories/player_repo.upsert_player) — producer не
обязан знать вообще все поля разом на каждый снапшот.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.orm import Session

from core.redis_stream import Handler, StreamFields
from models.player import Player
from repositories import player_repo, server_repo

logger = logging.getLogger(__name__)

# Протокольные константы — переименование ломает совместимость с тем, что
# напишет producer; менять вместе с Go-стороной, не независимо.
STREAM_NAME = "ark:stream:player_sighting"
GROUP_NAME = "ark_backend_player_ingest"


class PlayerSighting(BaseModel):
    server_ip: str
    platform_id: str
    character_name: str | None = None
    level: int | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    observed_at: datetime | None = None
    reported_by_account_id: uuid.UUID | None = None

    @field_validator("platform_id")
    @classmethod
    def _platform_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("platform_id must not be blank")
        return v


def parse_sighting(fields: StreamFields) -> PlayerSighting | None:
    """None — сообщение неисправимо кривое (не тот тип, отсутствует
    обязательное поле). Вызывающий (handle_player_message) на None
    считает сообщение "обработанным" (ACK, не ретрай) — до producer'а
    ничего не достучится повторной попыткой то же самое кривое сообщение."""
    try:
        return PlayerSighting.model_validate(fields)
    except ValidationError:
        logger.warning("player_ingest: malformed message, dropping: %r", fields)
        return None


def ingest_player_sighting(session: Session, sighting: PlayerSighting) -> Player:
    server = server_repo.get_or_create_server_by_ip(session, sighting.server_ip)
    player_repo.get_or_create_person(session, sighting.platform_id)
    now = datetime.now(UTC)
    return player_repo.upsert_player(
        session,
        server_id=server.id,
        platform_id=sighting.platform_id,
        character_name=sighting.character_name,
        level=sighting.level,
        x=sighting.x,
        y=sighting.y,
        z=sighting.z,
        last_seen_at=sighting.observed_at or now,
        updated_at=now,
        reported_by_account_id=sighting.reported_by_account_id,
    )


def _ingest_sync(session_factory: Callable[[], Session], fields: StreamFields) -> None:
    """Тело, реально идущее в поток (`asyncio.to_thread`) — SQLAlchemy
    `Session` синхронна, гонять её в event loop напрямую значит блокировать
    его на каждый апсерт."""
    sighting = parse_sighting(fields)
    if sighting is None:
        return
    with session_factory() as session:
        ingest_player_sighting(session, sighting)


def make_handler(session_factory: Callable[[], Session]) -> Handler:
    """Связывает `session_factory` с сигнатурой `core.redis_stream.Handler`
    (та ничего не знает про SQLAlchemy) — вызывающая сторона (main.py)
    передаёт результат сюда в `run_consumer`, не открывая сама, что внутри
    thread-обёртка."""

    async def handler(fields: StreamFields) -> None:
        await asyncio.to_thread(_ingest_sync, session_factory, fields)

    return handler
