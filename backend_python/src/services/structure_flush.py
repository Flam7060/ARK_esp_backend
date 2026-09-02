"""УСТАРЕЛО: пишет в models/structure_legacy.py (MVP-заглушку) через
structure_repo. Не удалено — запускается из main.py (start_scheduler).

Фоновая задача §8.3: перенос "грязных" построек из Redis-кэша (§8.2) в
Postgres. Интервал независим от частоты клиентских снимков — сам кэш уже
сглаживает частые апдейты в одну запись на прогон.

Парсинг Redis-строк в типы и оркестрация (SCAN -> upsert -> сброс dirty)
живут здесь; сам SQL — в repositories/structure_repo.py.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis
from sqlalchemy.orm import Session

from core.db import get_engine
from repositories import structure_repo
from services.redis_keys import structure_scan_pattern

logger = logging.getLogger(__name__)

FLUSH_INTERVAL_SECONDS = 5 * 60
STALE_AFTER_HOURS = 24


def _parse_key(key: str) -> tuple[str, str] | None:
    # "ark:tribe:{tribe_id}:structure:{struct_key}" — maxsplit=4 keeps any
    # further ':' inside struct_key intact (build_struct_key embeds several).
    parts = key.split(":", 4)
    if len(parts) != 5 or parts[0] != "ark" or parts[1] != "tribe" or parts[3] != "structure":
        return None
    return parts[2], parts[4]


def _to_float(v: str) -> float | None:
    return float(v) if v else None


def _to_int(v: str) -> int | None:
    return int(v) if v else None


def _to_bool(v: str) -> bool | None:
    return None if v == "" else v == "1"


async def flush_dirty_structures(redis: Redis, session_factory: Callable[[], Session]) -> int:
    """Один прогон: SCAN грязных ключей, upsert в Postgres, сброс dirty.

    Возвращает число фактически перенесённых построек — используется в
    логах/тестах, не в самой логике.
    """
    flushed = 0
    async for key in redis.scan_iter(match=structure_scan_pattern(), count=500):
        parsed = _parse_key(key)
        if parsed is None:
            logger.warning("structure_flush: unexpected key shape, skipping: %s", key)
            continue
        tribe_id, struct_key = parsed

        data = await redis.hgetall(key)
        if not data or data.get("dirty") != "1":
            continue

        try:
            await asyncio.to_thread(_upsert_from_cache, session_factory, tribe_id, struct_key, data)
        except Exception:
            # Шаг 3 §8.3: dirty сбрасывается только после успешной записи —
            # упавший прогон должен повторить эту запись на следующем тике,
            # а не потерять изменение молча.
            logger.exception("structure_flush: postgres upsert failed for %s", key)
            continue

        await redis.hset(key, "dirty", "0")
        flushed += 1
    return flushed


def _upsert_from_cache(
    session_factory: Callable[[], Session], tribe_id: str, struct_key: str, data: dict[str, str]
) -> None:
    # map_id закодирован вторым сегментом struct_key (см.
    # services.structure_store.build_struct_key) — извлекаем его отдельно,
    # потому что колонка map_id в Postgres отдельная (§8.3 DDL).
    map_id = struct_key.split(":", 2)[1] if struct_key.count(":") >= 2 else ""
    now = datetime.now(UTC)

    first_seen_raw = data.get("first_seen_at")
    last_seen_raw = data.get("last_seen_at")

    with session_factory() as session:
        structure_repo.upsert(
            session,
            tribe_id=tribe_id,
            map_id=map_id,
            struct_key=struct_key,
            first_seen_at=datetime.fromisoformat(first_seen_raw) if first_seen_raw else now,
            last_seen_at=datetime.fromisoformat(last_seen_raw) if last_seen_raw else now,
            class_=data.get("class", ""),
            kind_hint=data.get("kind_hint") or None,
            x=_to_float(data.get("x", "")),
            y=_to_float(data.get("y", "")),
            z=_to_float(data.get("z", "")),
            item_count=_to_int(data.get("item_count", "")),
            has_item_count=data.get("has_item_count") == "1",
            ammo=_to_float(data.get("ammo", "")),
            range_=_to_float(data.get("range", "")),
            enabled=_to_bool(data.get("enabled", "")),
            powered=_to_bool(data.get("powered", "")),
        )


def mark_stale_structures(session_factory: Callable[[], Session]) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=STALE_AFTER_HOURS)
    with session_factory() as session:
        return structure_repo.mark_stale(session, cutoff)


def start_scheduler(redis: Redis) -> AsyncIOScheduler:
    """Регистрирует и запускает периодический флаш. Вызывается один раз из
    main.py при старте приложения."""
    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)

    scheduler = AsyncIOScheduler()

    async def _job() -> None:
        n = await flush_dirty_structures(redis, session_factory)
        if n:
            logger.info("structure_flush: flushed %d structures", n)
        stale = await asyncio.to_thread(mark_stale_structures, session_factory)
        if stale:
            logger.info("structure_flush: marked %d structures possibly_demolished", stale)

    scheduler.add_job(_job, "interval", seconds=FLUSH_INTERVAL_SECONDS, id="structure_flush", max_instances=1)
    scheduler.start()
    return scheduler
