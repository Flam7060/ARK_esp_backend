"""Unit-тесты `core/api_key_cache.py` — с фейковым Redis-клиентом (сам
модуль дёргает только `set`/`getdel`/`delete`), никакого реального Redis
не нужно, тот же принцип, что уже применяют тесты wsserver/quicserver в
backend_go для GroupChecker."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core.api_key_cache import RedisApiKeyCache


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}

    def set(self, name: str, value: str, ex: int | None = None) -> None:
        self.store[name] = (value, ex)

    def getdel(self, name: str) -> str | None:
        entry = self.store.pop(name, None)
        return entry[0] if entry else None

    def delete(self, name: str) -> None:
        self.store.pop(name, None)


def test_set_key_writes_both_token_and_id_index():
    rdb = FakeRedis()
    cache = RedisApiKeyCache(rdb)
    account_id = uuid4()
    key_id = uuid4()
    token = "plaintext-token-value"

    cache.set_key(token, key_id, account_id, expires_at=None)

    digest = hashlib.sha256(token.encode()).hexdigest()
    assert rdb.store[f"ark:api_key:tok:{digest}"][0] == str(account_id)
    assert rdb.store[f"ark:api_key:id:{key_id}"][0] == digest


def test_set_key_ttl_matches_expires_at_when_sooner_than_cap():
    rdb = FakeRedis()
    cache = RedisApiKeyCache(rdb)
    account_id, key_id = uuid4(), uuid4()
    expires_at = datetime.now(UTC) + timedelta(seconds=120)

    cache.set_key("tok", key_id, account_id, expires_at)

    _, ttl = rdb.store[f"ark:api_key:id:{key_id}"]
    assert ttl is not None
    assert 0 < ttl <= 120


def test_set_key_skips_already_expired_key():
    rdb = FakeRedis()
    cache = RedisApiKeyCache(rdb)
    account_id, key_id = uuid4(), uuid4()
    expires_at = datetime.now(UTC) - timedelta(seconds=5)

    cache.set_key("tok", key_id, account_id, expires_at)

    assert rdb.store == {}


def test_delete_key_removes_both_entries():
    rdb = FakeRedis()
    cache = RedisApiKeyCache(rdb)
    account_id, key_id = uuid4(), uuid4()
    cache.set_key("tok", key_id, account_id, expires_at=None)

    cache.delete_key(key_id)

    assert rdb.store == {}


def test_delete_key_on_unknown_id_is_a_noop():
    rdb = FakeRedis()
    cache = RedisApiKeyCache(rdb)

    cache.delete_key(uuid4())  # must not raise

    assert rdb.store == {}
