"""Единственный Redis-клиент процесса ark_backend.

Тот же инстанс Redis, что читает и пишет ark_relay (см.
docs/telemetry-api-v1.md §8) — сервисы не имеют отдельных Redis: §8
описывает один источник истины для "сейчас", а не по одному на сервис.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as redis

from core.config import config

_pool: redis.ConnectionPool | None = None


def get_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(config.redis.url, decode_responses=True)
    return _pool


async def get_redis() -> AsyncGenerator[redis.Redis]:
    """FastAPI-зависимость: клиент на запрос, соединение берётся из пула."""
    client = redis.Redis(connection_pool=get_pool())
    try:
        yield client
    finally:
        await client.aclose()
