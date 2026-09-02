"""Синхронный Redis-клиент — только для `services/sharing_service.py`
через `core/group_cache.RedisGroupCache`. Отдельный от `core/redis.py`
(async), которым пользуются стрим-консьюмеры (`main.py` lifespan) — эти
роутеры (`routers/v1/groups.py`) обычные `def`, завязаны на синхронную
SQLAlchemy `Session`, заворачивать их в async ради одного Redis-клиента
не стоит."""

from __future__ import annotations

from collections.abc import Iterator

import redis

from core.config import config
from core.group_cache import RedisGroupCache

_pool: redis.ConnectionPool | None = None


def get_sync_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(config.redis.url, decode_responses=True)
    return _pool


def get_group_cache() -> Iterator[RedisGroupCache]:
    """FastAPI-зависимость: клиент на запрос, соединение берётся из пула."""
    client = redis.Redis(connection_pool=get_sync_pool())
    try:
        yield RedisGroupCache(client)
    finally:
        client.close()
