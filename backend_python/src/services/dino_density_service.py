"""Тепловая карта ручных дино: периодический замер живого Redis-слоя,
свёрнутый в ячейки сетки и записанный в `dino_density`.

Почему замер, а не поток. Остальные ingestion-сервисы висят на Redis
Stream: там каждое сообщение — факт, который надо сохранить. Плотность —
не поток фактов, а срез состояния: "сколько питомцев такого-то трайба
стоит в этой клетке прямо сейчас". Считать её из потока пришлось бы,
накапливая состояние в самом сервисе, тогда как живой слой уже держит
ровно это состояние с TTL — остаётся периодически его сложить.

Почему здесь, а не на релее. `ark_relay` документирован как не хранящий
состояния дольше жизни соединения и никогда не пишущий в Postgres
(telemetry-api-v1.md §7.2) — накопительный счётчик там ломал бы это
свойство.

Дедупликация достаётся бесплатно: в живом слое одна сущность — один хеш,
так что счёт по ячейке это счёт уникальных ключей, а не событий. Ровно
ради этого ключ дино на релее переехал с "dino:{label}:{team}" (где все
одноимённые животные схлопывались в один) на контент-адресуемый —
см. dedup.KeyForDino.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import config
from core.db import get_engine
from models.sharing import SharingGroup
from repositories import dino_density_repo, server_repo

logger = logging.getLogger(__name__)

_ROOM_SUFFIX = ":entities"


def _room_prefix(group_id: uuid.UUID | str) -> str:
    return f"ark:group:{group_id}:server:"


def server_ip_from_room_key(key: str, group_id: uuid.UUID | str) -> str | None:
    """Достаёт server_ip из ключа индекса комнаты.

    Разбором краёв, а не split(":"): server_ip это "ip:port", в нём есть
    собственное двоеточие, и наивное разбиение по разделителю отрезало бы
    порт (симптом — все сервера схлопываются в один, потому что
    get_or_create_server_by_ip получает адрес без порта).
    """
    prefix = _room_prefix(group_id)
    if not key.startswith(prefix) or not key.endswith(_ROOM_SUFFIX):
        return None
    server_ip = key[len(prefix) : -len(_ROOM_SUFFIX)]
    return server_ip or None


def cell_index(value: float, cell_size_units: int) -> int:
    """Номер ячейки по одной оси.

    math.floor, а не int(): координаты ARK бывают отрицательными, а int()
    округляет к нулю — клетки по разные стороны от нуля слипались бы
    попарно (симптом — полкарты с удвоенной плотностью).
    """
    return math.floor(value / cell_size_units)


def bucket_start_for(moment: datetime, bucket_seconds: int) -> datetime:
    """Начало временного окна, в которое попадает moment."""
    epoch_seconds = int(moment.timestamp())
    return datetime.fromtimestamp(epoch_seconds - epoch_seconds % bucket_seconds, tz=UTC)


async def _collect_room(
    redis: Redis, room_key: str, floor_ms: float, cell_size_units: int
) -> dict[tuple[int, int, str, int], int]:
    """Считает ручных дино живой комнаты по ячейкам.

    Ключ результата — (cell_x, cell_y, tribe_name, team): трайб на этом
    этапе ещё строка с провода, в tribe_id он превращается уже на
    Postgres-стороне (там же, где и у остальных ingestion-сервисов).
    """
    members = await redis.zrangebyscore(room_key, min=floor_ms, max="+inf")
    if not members:
        return {}

    entity_prefix = room_key[: -len(_ROOM_SUFFIX)] + ":entity:"
    pipe = redis.pipeline()
    for member in members:
        pipe.hmget(entity_prefix + member, ["cat", "tamed", "team", "tribe", "x", "y"])
    rows = await pipe.execute()

    counts: dict[tuple[int, int, str, int], int] = defaultdict(int)
    for row in rows:
        # Хеш мог истечь между чтением индекса и этим HMGET -- индекс
        # релей не чистит, так что дырки тут норма, а не сбой.
        if not row or row[0] != "dino" or row[1] != "1":
            continue
        tribe = row[3] or ""
        if not tribe:
            # Питомец без читаемого трайба не отвечает на вопрос "чья
            # масса" -- см. dino_density.tribe_id (NOT NULL).
            continue
        try:
            team = int(row[2] or 0)
            x = float(row[4])
            y = float(row[5])
        except (TypeError, ValueError):
            continue
        counts[(cell_index(x, cell_size_units), cell_index(y, cell_size_units), tribe, team)] += 1
    return counts


def _write_cells(
    session_factory: Callable[[], Session],
    server_ip: str,
    counts: dict[tuple[int, int, str, int], int],
    observed_at: datetime,
) -> int:
    """Синхронная часть: резолв сервера/трайба и запись ячеек."""
    cell_size = config.density.CELL_SIZE_UNITS
    bucket = bucket_start_for(observed_at, config.density.BUCKET_SECONDS)
    written = 0
    with session_factory() as session:
        server = server_repo.get_or_create_server_by_ip(session, server_ip)
        for (cell_x, cell_y, tribe_name, team), count in counts.items():
            tribe = server_repo.get_or_create_tribe(session, server.id, tribe_name, ark_tribe_id=team)
            if tribe is None:
                continue
            dino_density_repo.upsert_cell(
                session,
                server_id=server.id,
                tribe_id=tribe.id,
                cell_size_units=cell_size,
                cell_x=cell_x,
                cell_y=cell_y,
                bucket_start=bucket,
                count=count,
                observed_at=observed_at,
                # Плотность собрана из наблюдений всей группы, одного
                # автора у строки нет -- в отличие от штучных сайтингов,
                # где reported_by это конкретный клиент.
                reported_by_account_id=None,
            )
            written += 1
    return written


def _group_ids(session_factory: Callable[[], Session]) -> list[uuid.UUID]:
    with session_factory() as session:
        return list(session.execute(select(SharingGroup.id)).scalars())


async def sample_once(redis: Redis, session_factory: Callable[[], Session]) -> int:
    """Один замер по всем группам. Возвращает число записанных ячеек."""
    observed_at = datetime.now(UTC)
    floor_ms = (observed_at - timedelta(seconds=config.density.LIVE_FLOOR_SECONDS)).timestamp() * 1000
    cell_size = config.density.CELL_SIZE_UNITS

    group_ids = await asyncio.to_thread(_group_ids, session_factory)
    written = 0
    for group_id in group_ids:
        # SCAN ограничен префиксом одной группы: сами группы берём из
        # Postgres (детерминированно), а вот на каких серверах группа
        # сейчас видна -- знает только Redis, списка "группа -> сервера"
        # нигде нет.
        async for room_key in redis.scan_iter(match=f"{_room_prefix(group_id)}*{_ROOM_SUFFIX}"):
            server_ip = server_ip_from_room_key(room_key, group_id)
            if server_ip is None:
                continue
            counts = await _collect_room(redis, room_key, floor_ms, cell_size)
            if not counts:
                continue
            written += await asyncio.to_thread(_write_cells, session_factory, server_ip, counts, observed_at)
    return written


def start_scheduler(redis: Redis) -> AsyncIOScheduler:
    """Регистрирует и запускает периодический замер. Вызывается один раз
    из main.py при старте приложения (тот же образец, что
    structure_flush.start_scheduler)."""
    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)

    scheduler = AsyncIOScheduler()

    async def _job() -> None:
        written = await sample_once(redis, session_factory)
        if written:
            logger.info("dino_density: обновлено ячеек: %d", written)

    scheduler.add_job(
        _job,
        "interval",
        seconds=config.density.INTERVAL_SECONDS,
        id="dino_density",
        max_instances=1,
    )
    scheduler.start()
    return scheduler
