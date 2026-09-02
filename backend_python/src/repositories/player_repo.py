"""Репозиторий `player`/`person` для ingestion из Redis Stream — persistence
без бизнес-правил (парсинг сообщения — services/player_ingest_service.py).
`Person` и `Player` апсертятся в одной функции: `Player.platform_id` — FK
на `Person.platform_id`, вставить Player без предварительного Person
невозможно физически (FK constraint), так что порядок не опционален."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.player import Player
from models.topology import Person


def get_or_create_person(session: Session, platform_id: str) -> Person:
    person = session.get(Person, platform_id)
    if person is None:
        person = Person(platform_id=platform_id)
        session.add(person)
        session.flush()  # платформа должна существовать до insert Player (FK)
    return person


def upsert_player(
    session: Session,
    *,
    server_id: uuid.UUID,
    platform_id: str,
    character_name: str | None,
    level: int | None,
    x: float | None,
    y: float | None,
    z: float | None,
    last_seen_at: datetime,
    updated_at: datetime,
    reported_by_account_id: uuid.UUID | None,
) -> Player:
    """(server_id, platform_id) — уникальный ключ (models/player.py), тот же
    принцип, что структуры: у сущности нет стабильного игрового id между
    сессиями наблюдения, идентичность держит связка сервер+платформенный id.

    Поля, не пришедшие в сообщении (None), не затирают уже сохранённые —
    Go может прислать частичное обновление (например, только позицию),
    затирать character_name/level молчанием было бы потерей данных."""
    stmt = select(Player).where(Player.server_id == server_id, Player.platform_id == platform_id)
    player = session.execute(stmt).scalar_one_or_none()
    if player is None:
        player = Player(server_id=server_id, platform_id=platform_id)
        session.add(player)

    if character_name is not None:
        player.character_name = character_name
    if level is not None:
        player.level = level
    if x is not None:
        player.x = x
    if y is not None:
        player.y = y
    if z is not None:
        player.z = z

    player.last_seen_at = last_seen_at
    player.updated_at = updated_at
    if reported_by_account_id is not None:
        player.last_updated_by_account_id = reported_by_account_id

    session.commit()
    session.refresh(player)
    return player
