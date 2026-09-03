"""Сервис ingestion `ark_structure`/`ark_structure_turret` из Redis Stream
`ark:stream:structure_sighting` -- второй конкретный consumer поверх
core/redis_stream.py, тем же образцом, что services/player_ingest_service.py
для игроков. Продюсер -- `ark_relay` (Go), `internal/streamproducer` +
`internal/hub`'s дедуп-гейт; контракт полей (XADD Values) определён там,
здесь -- его pydantic-зеркало.

Турели и обычные структуры идут одним стримом (турель -- это структура с
доп. полями и в самой Postgres-схеме, ark_structure_turret -- 1:1
расширение ark_structure, не отдельная сущность) -- различаются по
присутствию turret_*-полей, а не по отдельному стриму."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from core.redis_stream import Handler, StreamFields
from repositories import ark_lookups_repo, ark_structure_repo, server_repo

logger = logging.getLogger(__name__)

# Протокольные константы — переименование ломает совместимость с тем, что
# пишет ark_relay (internal/streamproducer.StreamNameFor); менять вместе с
# Go-стороной, не независимо.
STREAM_NAME = "ark:stream:structure_sighting"
GROUP_NAME = "ark_backend_structure_ingest"


def _parse_bool(value: str | bool | None) -> bool | None:
    """Redis Stream fields — всегда str (core.redis_stream.StreamFields =
    dict[str, str]); Go пишет ровно "true"/"false" (strconv.FormatBool),
    разбираем явно вместо надежды на неявную коэрсию pydantic."""
    if value is None or isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"expected 'true'/'false', got {value!r}")


class StructureSighting(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    server_ip: str
    object_hash: str
    class_code: str | None = Field(default=None, alias="class")
    tribe_name: str | None = None
    team: int | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    health: float | None = None
    max_health: float | None = None
    turret_ammo: int | None = None
    turret_range: float | None = None
    turret_powered: bool | None = None
    turret_active: bool | None = None
    removed: bool = False
    observed_at: datetime | None = None
    reported_by_account_id: str | None = None
    reported_by_character_id: str | None = None

    _parse_turret_powered = field_validator("turret_powered", mode="before")(_parse_bool)
    _parse_turret_active = field_validator("turret_active", mode="before")(_parse_bool)
    _parse_removed = field_validator("removed", mode="before")(_parse_bool)

    @field_validator("object_hash")
    @classmethod
    def _object_hash_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("object_hash must not be blank")
        return v


def parse_sighting(fields: StreamFields) -> StructureSighting | None:
    """None — сообщение неисправимо кривое. Вызывающий на None считает
    сообщение обработанным (ACK, не ретрай) — до Go-продюсера повторной
    попыткой то же кривое сообщение не достучаться, см.
    player_ingest_service.parse_sighting за тот же выбор."""
    try:
        return StructureSighting.model_validate(fields)
    except ValidationError:
        logger.warning("structure_sighting: malformed message, dropping: %r", fields)
        return None


def ingest_structure_sighting(session: Session, sighting: StructureSighting) -> None:
    server = server_repo.get_or_create_server_by_ip(session, sighting.server_ip)

    if sighting.removed:
        # Явный сигнал "пропало" (Inbound.Vanished, DTO-sharing plan §5b) —
        # class_code не приходит с этим сообщением (Go не пересчитывает его
        # из уже пропавшей цели), поэтому здесь нечего апсертить, только
        # удалить по (server_id, object_hash).
        ark_structure_repo.delete_by_hash(session, server_id=server.id, object_hash=sighting.object_hash)
        return

    if sighting.class_code is None:
        logger.warning("structure_sighting: non-removed message missing class, dropping: %s", sighting.object_hash)
        return

    is_turret = sighting.turret_ammo is not None or sighting.turret_range is not None or sighting.turret_powered is not None
    structure_class = ark_lookups_repo.get_or_create_structure_class(session, sighting.class_code, is_turret=is_turret)

    tribe = server_repo.get_or_create_tribe(session, server.id, sighting.tribe_name, ark_tribe_id=sighting.team)

    reported_by = uuid.UUID(sighting.reported_by_account_id) if sighting.reported_by_account_id else None
    observed_at = sighting.observed_at or datetime.now(UTC)

    structure = ark_structure_repo.upsert_structure(
        session,
        server_id=server.id,
        object_hash=sighting.object_hash,
        class_code=structure_class.code,
        tribe_id=tribe.id if tribe else None,
        x=sighting.x,
        y=sighting.y,
        z=sighting.z,
        health=sighting.health,
        observed_at=observed_at,
        reported_by_account_id=reported_by,
    )

    if is_turret:
        ark_structure_repo.upsert_turret(
            session,
            structure_id=structure.id,
            ammo_count=sighting.turret_ammo,
            range_=sighting.turret_range,
            is_active=sighting.turret_active,
        )


def _ingest_sync(session_factory: Callable[[], Session], fields: StreamFields) -> None:
    """Тело, реально идущее в поток (asyncio.to_thread) — SQLAlchemy Session
    синхронна, гонять её в event loop напрямую значит блокировать его на
    каждый апсерт (см. player_ingest_service._ingest_sync за тот же выбор)."""
    sighting = parse_sighting(fields)
    if sighting is None:
        return
    with session_factory() as session:
        ingest_structure_sighting(session, sighting)


def make_handler(session_factory: Callable[[], Session]) -> Handler:
    async def handler(fields: StreamFields) -> None:
        await asyncio.to_thread(_ingest_sync, session_factory, fields)

    return handler
