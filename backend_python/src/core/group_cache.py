"""Redis-зеркало членства групп для `backend_go` (DTO-sharing plan §5):
`ark_relay` проверяет допуск на WS-коннект через `SISMEMBER
ark:group:{id}:members`, а мгновенный отзыв при кике — через
`PSUBSCRIBE ark:group:*:revoked`. Источник истины — Postgres
(`group_member`), это только читаемый Go-стороной слепок, обновляемый
синхронно с каждой мутацией членства в `services/sharing_service.py`.

`GroupCache` — узкий протокол (не сам `redis.Redis`), чтобы
`sharing_service` не тянул за собой живой Redis в юнит-тестах — тот же
принцип, что интерфейсы `EntityWriter`/`HashCache`/`Publisher` в
`backend_go` (fakes вместо поднятого Redis)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    import redis


class GroupCache(Protocol):
    def add_member(self, group_id: UUID, account_id: UUID) -> None: ...

    def remove_member(self, group_id: UUID, account_id: UUID) -> None: ...

    def delete_group(self, group_id: UUID, member_ids: list[UUID]) -> None: ...


def _members_key(group_id: UUID) -> str:
    return f"ark:group:{group_id}:members"


def _revoked_channel(group_id: UUID) -> str:
    return f"ark:group:{group_id}:revoked"


class RedisGroupCache:
    """Реализация поверх синхронного `redis.Redis` — sharing_service
    работает над синхронной SQLAlchemy `Session` (роутеры `groups.py` —
    обычные `def`, не `async def`), отдельный клиент от async-стороны
    (`core/redis.py`), которой пользуются стрим-консьюмеры."""

    def __init__(self, rdb: "redis.Redis") -> None:
        self._rdb = rdb

    def add_member(self, group_id: UUID, account_id: UUID) -> None:
        self._rdb.sadd(_members_key(group_id), str(account_id))

    def remove_member(self, group_id: UUID, account_id: UUID) -> None:
        self._rdb.srem(_members_key(group_id), str(account_id))
        # PUBLISH даже если SREM ничего не удалил (уже не член) — отзыв
        # должен закрыть любую активную WS-сессию этого account_id в этой
        # группе на Go-стороне, независимо от того, откуда инициирован кик.
        self._rdb.publish(_revoked_channel(group_id), str(account_id))

    def delete_group(self, group_id: UUID, member_ids: list[UUID]) -> None:
        # Публикуем отзыв на каждого участника ДО удаления ключа членства —
        # иначе Go, получив revoked-сигнал, теоретически успел бы сделать
        # неактуальный повторный SISMEMBER на ещё существующий ключ (не
        # критично для этого пути — revoke закрывает WS напрямую по
        # (group_id, account_id), не перепроверяя SISMEMBER — но порядок
        # "сначала сигнал, потом снос ключа" безопаснее в обе стороны).
        for account_id in member_ids:
            self._rdb.publish(_revoked_channel(group_id), str(account_id))
        self._rdb.delete(_members_key(group_id))
