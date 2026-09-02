"""Репозиторий `ark_structure`/`ark_structure_turret` для ingestion из
Redis Stream (services/structure_sighting_service.py) -- persistence без
бизнес-правил, тем же принципом, что repositories/player_repo.py.

Дедупликация корректности (не трафика -- та уже сделана Go-стороной, см.
DTO-sharing plan §2/§3) на этом уровне держит сама Postgres-схема:
`UNIQUE(server_id, object_hash)` + `ON CONFLICT DO UPDATE` -- два
одинаковых факта схлопнутся в одну строку сами, репозиторию не нужно
самому проверять "уже было?" перед вставкой."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.ark_structure import ArkStructure, ArkStructureTurret


def upsert_structure(
    session: Session,
    *,
    server_id: uuid.UUID,
    object_hash: str,
    class_code: str,
    tribe_id: uuid.UUID | None,
    x: float | None,
    y: float | None,
    z: float | None,
    health: float | None,
    observed_at: datetime,
    reported_by_account_id: uuid.UUID | None,
) -> ArkStructure:
    """(server_id, object_hash) -- уникальный ключ (models/ark_structure.py),
    первое наблюдение заводит first_seen_at, дальше он не трогается --
    только last_seen_at продвигается вперёд, как и last_updated_by."""
    stmt = select(ArkStructure).where(ArkStructure.server_id == server_id, ArkStructure.object_hash == object_hash)
    structure = session.execute(stmt).scalar_one_or_none()

    if structure is None:
        structure = ArkStructure(
            server_id=server_id, object_hash=object_hash, class_code=class_code, first_seen_at=observed_at
        )
        session.add(structure)

    structure.class_code = class_code
    if tribe_id is not None:
        structure.tribe_id = tribe_id
    if x is not None:
        structure.x = x
    if y is not None:
        structure.y = y
    if z is not None:
        structure.z = z
    if health is not None:
        structure.health = health

    structure.last_seen_at = observed_at
    structure.updated_at = observed_at
    if reported_by_account_id is not None:
        structure.last_updated_by_account_id = reported_by_account_id

    session.commit()
    session.refresh(structure)
    return structure


def upsert_turret(
    session: Session,
    *,
    structure_id: uuid.UUID,
    ammo_count: int | None,
    range_: float | None,
    is_active: bool | None,
) -> ArkStructureTurret:
    """1:1 расширение (models/ark_structure.py) -- structure_id одновременно
    PK и FK, ON DELETE CASCADE делает эту строку бессмысленной без
    родителя, поэтому вызывающий (структура уже апсертнута выше) всегда
    успевает создать/найти родителя раньше."""
    turret = session.get(ArkStructureTurret, structure_id)
    if turret is None:
        turret = ArkStructureTurret(structure_id=structure_id)
        session.add(turret)

    if ammo_count is not None:
        turret.ammo_count = ammo_count
    if range_ is not None:
        turret.range = range_
    if is_active is not None:
        turret.is_active = is_active

    # Динамическое состояние турели редко доходит сюда вообще -- Go
    # гейтит дедуп по неизменяемым полям (class+координаты), не по этим;
    # см. DTO-sharing plan §2. История изменений ammo/powered отдельно от
    # "последнее известное состояние" -- отдельная таблица, не эта.
    session.commit()
    session.refresh(turret)
    return turret


def delete_by_hash(session: Session, *, server_id: uuid.UUID, object_hash: str) -> bool:
    """Явный сигнал "пропало" (Inbound.Vanished -> removed=true, DTO-sharing
    plan §5b) -- immediate удаление, не пометка статуса: у ark_structure
    (в отличие от устаревшей structure_legacy) нет status-колонки, а кто-то
    лично подтвердил снос, так что откладывать до порога свежести незачем.
    Возвращает False, если строки уже не было (сообщение о сносе пришло
    повторно, или строка никогда не создавалась) -- не ошибка."""
    structure = session.execute(
        select(ArkStructure).where(ArkStructure.server_id == server_id, ArkStructure.object_hash == object_hash)
    ).scalar_one_or_none()
    if structure is None:
        return False
    session.delete(structure)  # cascade снимает turret/generator вместе с ней
    session.commit()
    return True
