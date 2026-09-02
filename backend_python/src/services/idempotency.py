"""FR-2: повторная отправка того же снимка (сетевой ретрай) не создаёт
дублей. Идемпотентность по snapshot_id, TTL — не навсегда, чтобы ключи не
копились бесконечно; ретраи после суток простоя сети — не тот случай,
который стоит поддерживать бесплатно.
"""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis

_TTL_SECONDS = 24 * 60 * 60


def _key(snapshot_id: UUID) -> str:
    return f"ark:snapshot:{snapshot_id}"


async def already_processed(redis: Redis, snapshot_id: UUID) -> bool:
    return bool(await redis.exists(_key(snapshot_id)))


async def mark_processed(redis: Redis, snapshot_id: UUID) -> None:
    await redis.set(_key(snapshot_id), "1", ex=_TTL_SECONDS)
