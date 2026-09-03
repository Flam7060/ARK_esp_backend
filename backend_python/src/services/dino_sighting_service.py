"""Сервис ingestion `tamed_dino` из Redis Stream `ark:stream:dino_sighting`
— третий consumer поверх core/redis_stream.py, тем же образцом, что
services/player_ingest_service.py/structure_sighting_service.py. Продюсер —
`ark_relay` (Go), тот же `internal/streamproducer.EntityFields`, что и для
структур (общий формат полей, разные стримы и модели-получатели)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.orm import Session

from core.redis_stream import Handler, StreamFields
from repositories import ark_lookups_repo, server_repo, tamed_dino_repo

logger = logging.getLogger(__name__)

STREAM_NAME = "ark:stream:dino_sighting"
GROUP_NAME = "ark_backend_dino_ingest"


class DinoSighting(BaseModel):
    server_ip: str
    object_hash: str
    class_: str | None = None  # alias would collide with Python's "class" keyword; set manually below
    tribe_name: str | None = None
    team: int | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    health: float | None = None
    observed_at: datetime | None = None
    reported_by_account_id: str | None = None
    reported_by_character_id: str | None = None

    @field_validator("object_hash")
    @classmethod
    def _object_hash_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("object_hash must not be blank")
        return v


def parse_sighting(fields: StreamFields) -> DinoSighting | None:
    """None — сообщение неисправимо кривое (см. player_ingest_service для
    того же выбора: обрабатывается как ACK, не ретрай)."""
    # "class" -- зарезервированное слово Python, model_validate с alias'ом
    # решает это для structure_sighting_service (Field(alias="class")), но
    # здесь тот же трюк конфликтовал бы с population-режимом по имени; проще
    # переложить одно поле руками перед валидацией, чем городить второй
    # ConfigDict ради одного алиаса.
    remapped = dict(fields)
    if "class" in remapped:
        remapped["class_"] = remapped.pop("class")
    try:
        return DinoSighting.model_validate(remapped)
    except ValidationError:
        logger.warning("dino_sighting: malformed message, dropping: %r", fields)
        return None


def ingest_dino_sighting(session: Session, sighting: DinoSighting) -> None:
    if sighting.class_ is None:
        logger.warning("dino_sighting: message missing class, dropping: %s", sighting.object_hash)
        return

    server = server_repo.get_or_create_server_by_ip(session, sighting.server_ip)
    species = ark_lookups_repo.get_or_create_species(session, sighting.class_)
    tribe = server_repo.get_or_create_tribe(session, server.id, sighting.tribe_name, ark_tribe_id=sighting.team)

    reported_by = uuid.UUID(sighting.reported_by_account_id) if sighting.reported_by_account_id else None
    observed_at = sighting.observed_at or datetime.now(UTC)

    tamed_dino_repo.upsert_dino(
        session,
        server_id=server.id,
        species_id=species.id,
        object_hash=sighting.object_hash,
        tribe_id=tribe.id if tribe else None,
        x=sighting.x,
        y=sighting.y,
        z=sighting.z,
        health=sighting.health,
        observed_at=observed_at,
        reported_by_account_id=reported_by,
    )


def _ingest_sync(session_factory: Callable[[], Session], fields: StreamFields) -> None:
    sighting = parse_sighting(fields)
    if sighting is None:
        return
    with session_factory() as session:
        ingest_dino_sighting(session, sighting)


def make_handler(session_factory: Callable[[], Session]) -> Handler:
    async def handler(fields: StreamFields) -> None:
        await asyncio.to_thread(_ingest_sync, session_factory, fields)

    return handler
