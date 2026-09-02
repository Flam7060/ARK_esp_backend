"""Сервис `api_key` — self-service: account создаёт/отзывает свои
собственные ключи, шаблон — services/activation_key_service.py, но
изоляция по account_id на каждой операции (см. repositories/api_key_repo)
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from core.api_key_cache import ApiKeyCache
from core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from core.tokens import generate_token, hash_token
from models.api_key import ApiKey, ApiKeyScope
from repositories import api_key_repo
from routers.v1.schemas.api_key import ApiKeyCreate

DEFAULT_LIMIT = 200
MAX_LIMIT = 500

# Не для секретности (весь токен и так секрет) — для узнаваемости своего
# ключа в списке без хранения/показа плейнтекста повторно.
_PREFIX_LEN = 8
_LAST4_LEN = 4

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "InvalidCursorError",
    "NotFoundError",
    "create_api_key",
    "get_api_key",
    "list_api_keys",
    "revoke_api_key",
]


class NotFoundError(Exception):
    """Ключ с таким id не существует ИЛИ принадлежит другому account —
    repositories.api_key_repo.get_by_id_for_account фильтрует по
    account_id в самом запросе, так что оба случая неотличимы уже на
    уровне SQL: чужому account'у не за чем подтверждать даже факт
    существования ключа."""


def create_api_key(
    session: Session, account_id: UUID, data: ApiKeyCreate, cache: ApiKeyCache | None = None
) -> tuple[ApiKey, str]:
    token = generate_token()
    api_key = ApiKey(
        account_id=account_id,
        key_hash=hash_token(token),
        prefix=token[:_PREFIX_LEN],
        last4=token[-_LAST4_LEN:],
        expires_at=data.expires_at,
    )
    api_key.scopes = [ApiKeyScope(scope=s) for s in dict.fromkeys(data.scopes)]  # dedup, порядок сохранён
    api_key_repo.insert(session, api_key)
    if cache is not None:
        # После insert — api_key.id уже проставлен autoincrement/default'ом
        # (см. insert()'s session.refresh). Postgres остаётся источником
        # истины даже если эта запись в Redis почему-то не удастся: ключ
        # просто не заработает на relay-стороне (backend_go) до следующей
        # попытки, HTTP-путь (routers/v1/api_keys.py) от Redis не зависит.
        cache.set_key(token, api_key.id, account_id, api_key.expires_at)
    return api_key, token


def get_api_key(session: Session, key_id: UUID, account_id: UUID) -> ApiKey:
    api_key = api_key_repo.get_by_id_for_account(session, key_id, account_id)
    if api_key is None:
        raise NotFoundError(f"api_key {key_id} not found")
    return api_key


def list_api_keys(
    session: Session, account_id: UUID, cursor: str | None, limit: int
) -> tuple[list[ApiKey], str | None]:
    limit = max(1, min(limit, MAX_LIMIT))
    after = decode_cursor(cursor) if cursor else None

    rows = api_key_repo.list_page_for_account(session, account_id, after, limit + 1)

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at, last.id)

    return rows, next_cursor


def revoke_api_key(session: Session, key_id: UUID, account_id: UUID, cache: ApiKeyCache | None = None) -> ApiKey:
    """Отзыв — не DELETE строки: `api_key_status.revoked` терминален
    (`is_terminal=True`), но исторический факт "такой ключ существовал и
    был отозван тогда-то" сохраняется, как и у `activation_key`."""
    api_key = get_api_key(session, key_id, account_id)
    api_key.status_code = "revoked"
    session.commit()
    session.refresh(api_key)
    if cache is not None:
        # После commit, не до — если Postgres откатится, кэш в Redis не
        # должен опустеть раньше самой записи о ревокации.
        cache.delete_key(api_key.id)
    return api_key
