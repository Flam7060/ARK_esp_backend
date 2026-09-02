"""Redis-зеркало `api_key` → `account_id` для `backend_go` (ark_relay):
`internal/authjwt.Verify` проверяет только RS256 JWT офлайн, никогда не
ходит в Postgres/ark_backend — self-service `api_key` (опаковый токен,
не JWT) ему нечем проверить без этого моста. Тот же принцип, что
`core/group_cache.py` для членства в группах: Postgres (`api_key`)
остаётся источником истины, Redis — только читаемый Go-стороной кэш,
обновляемый синхронно с каждой мутацией в `services/api_key_service.py`.

Кэш-ключ — НЕ `hash_token()` (HMAC-SHA256 с `SECURITY_PEPPER`, которым
хеширован сам `api_key.key_hash` в Postgres) — тот пеппер известен только
`ark_backend`, шарить его с Go ради этого не стоит: Redis здесь чисто
внутренняя инфраструктура (нет внешнего порта в docker-compose), а не
защита от дампа БД, так что для СПОСОБА, которым Go находит запись по
предъявленному ключу, достаточно обычного SHA-256(plaintext) — этот
модуль сам считает его один раз при записи, Go считает точно так же при
чтении (`internal/apikeycache`), без общего секрета между сервисами."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    import redis

# Потолок TTL даже для ключа без expires_at — на случай, если
# delete_key почему-то не отработает (падение между commit'ом и записью в
# Redis), устаревшая запись не переживает вечно.
_MAX_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 дней


def _cache_digest(token: str) -> str:
    """Непеппер-ованный SHA-256 — см. doc comment модуля про то, почему
    здесь это нормально в отличие от `core/tokens.hash_token`."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_key(digest: str) -> str:
    return f"ark:api_key:tok:{digest}"


def _id_key(key_id: UUID) -> str:
    # Вторичный индекс id -> digest: revoke_api_key знает только key_id
    # (плейнтекст токена больше нигде не хранится, "второго шанса
    # прочитать его нет" — см. ApiKey.key_hash), но чтобы снести запись
    # из Redis по токену, нужен именно digest — этот индекс его находит.
    return f"ark:api_key:id:{key_id}"


class ApiKeyCache(Protocol):
    def set_key(self, token: str, key_id: UUID, account_id: UUID, expires_at: datetime | None) -> None: ...

    def delete_key(self, key_id: UUID) -> None: ...


class RedisApiKeyCache:
    def __init__(self, rdb: "redis.Redis") -> None:
        self._rdb = rdb

    def set_key(self, token: str, key_id: UUID, account_id: UUID, expires_at: datetime | None) -> None:
        ttl = _MAX_TTL_SECONDS
        if expires_at is not None:
            remaining = int((expires_at - datetime.now(UTC)).total_seconds())
            if remaining <= 0:
                return  # уже истёк — кэшировать нечего
            ttl = min(ttl, remaining)
        digest = _cache_digest(token)
        self._rdb.set(_token_key(digest), str(account_id), ex=ttl)
        self._rdb.set(_id_key(key_id), digest, ex=ttl)

    def delete_key(self, key_id: UUID) -> None:
        # GETDEL — одним round-trip'ом достать digest и убрать индекс;
        # без него окно между GET и DEL могло бы оставить сироту в
        # ark:api_key:id:* (не критично — тот же TTL сам подчистит и её —
        # но лишний round-trip всё равно не нужен, раз есть GETDEL).
        digest = self._rdb.getdel(_id_key(key_id))
        if digest is not None:
            self._rdb.delete(_token_key(digest))
