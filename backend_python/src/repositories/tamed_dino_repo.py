"""Репозиторий `tamed_dino` для ingestion из Redis Stream
(services/dino_sighting_service.py).

В отличие от `ark_structure`/`player`, у дино нет настоящего уникального
ключа -- `object_hash` в models/tamed_dino.py прямо помечен "СИГНАЛ, не
unique". Идентичность подтверждается близостью координат между кадрами
здесь, в сервисном слое, а не составным UNIQUE в схеме (тот же принцип, что
задокументирован в докстринге модели) -- дино двигается, а два разных дино
одного вида/окраса, стоящих рядом (два ручных рекса на одной базе),
физически неотличимы одним хешем, и тянуть их в одну строку было бы хуже,
чем изредка завести дубликат."""

from __future__ import annotations

import math
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.tamed_dino import TamedDino

# Дино может пройти между двумя снимками команды дальше, чем 300-юнитовая
# сетка структур (структуры не ходят вовсе) -- радиус щедрее, но не
# бесконечный: за его пределами это уже, скорее всего, другое животное,
# а не то же самое, ушедшее далеко.
_MATCH_RADIUS = 3000.0


def _distance_sq(a_x: float | None, a_y: float | None, a_z: float | None, x: float, y: float, z: float) -> float:
    if a_x is None or a_y is None or a_z is None:
        return math.inf
    return (a_x - x) ** 2 + (a_y - y) ** 2 + (a_z - z) ** 2


def upsert_dino(
    session: Session,
    *,
    server_id: uuid.UUID,
    species_id: uuid.UUID,
    object_hash: str | None,
    tribe_id: uuid.UUID | None,
    x: float | None,
    y: float | None,
    z: float | None,
    health: float | None,  # noqa: ARG001 -- зарезервировано: TamedDino сегодня здоровье не хранит
    observed_at: datetime,
    reported_by_account_id: uuid.UUID | None,
) -> TamedDino:
    """Ищет ближайшего того же вида с тем же object_hash в радиусе
    _MATCH_RADIUS; не находит -- заводит новую строку. Кандидатов на один
    вид+хеш на одном сервере обычно единицы, полный перебор по ним дешевле,
    чем городить пространственный индекс ради этого объёма."""
    dino = None
    if x is not None and y is not None and z is not None:
        candidates = session.execute(
            select(TamedDino).where(
                TamedDino.server_id == server_id,
                TamedDino.species_id == species_id,
                TamedDino.object_hash == object_hash,
            )
        ).scalars()
        best_distance_sq = _MATCH_RADIUS * _MATCH_RADIUS
        for candidate in candidates:
            d = _distance_sq(candidate.x, candidate.y, candidate.z, x, y, z)
            if d <= best_distance_sq:
                best_distance_sq = d
                dino = candidate

    if dino is None:
        dino = TamedDino(server_id=server_id, species_id=species_id, object_hash=object_hash)
        session.add(dino)

    if tribe_id is not None:
        dino.tribe_id = tribe_id
    if x is not None:
        dino.x = x
    if y is not None:
        dino.y = y
    if z is not None:
        dino.z = z

    dino.last_seen_at = observed_at
    dino.updated_at = observed_at
    if reported_by_account_id is not None:
        dino.last_updated_by_account_id = reported_by_account_id

    session.commit()
    session.refresh(dino)
    return dino
