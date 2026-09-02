"""Сервис `activation_key` — бизнес-правила поверх repositories
/activation_key_repo.py, шаблон — services/user_service.py.

`create_activation_key` — единственное место, где плейнтекст токена вообще
существует: генерируется, хешируется, кладётся в БД только хеш, а
плейнтекст возвращается вызывающему и нигде не сохраняется — тот же
паттерн, что `api_key`/`group_invite_token` (см. их модели: "плейнтекст
1 раз").
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.pagination import InvalidCursorError, decode_cursor, encode_cursor
from core.tokens import generate_token, hash_token
from models.activation_key import ActivationKey
from repositories import activation_key_repo
from routers.v1.schemas.activation_key import ActivationKeyCreate, ActivationKeyUpdate

DEFAULT_LIMIT = 200
MAX_LIMIT = 500

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "ConflictError",
    "InvalidCursorError",
    "NotFoundError",
    "NotDeletableError",
    "create_activation_key",
    "delete_activation_key",
    "get_activation_key",
    "list_activation_keys",
    "update_activation_key",
]


class NotFoundError(Exception):
    """Ключ с таким id не существует."""


class ConflictError(Exception):
    """Коллизия token_hash — практически невозможна при 256 битах
    энтропии, но не невозможна: гонка ловится на уровне БД, не приложения."""


class NotDeletableError(Exception):
    """Ключ уже погашен — удалять погашенный ключ нельзя: это стёрло бы
    единственную запись о том, каким ключом активировал подписку
    конкретный account (redeemed_by_account_id), обрывая audit-цепочку."""


def create_activation_key(session: Session, data: ActivationKeyCreate) -> tuple[ActivationKey, str]:
    """Возвращает (строка_БД, плейнтекст_токена) — плейнтекст существует
    только в этом кадре стека, дальше он невосстановим."""
    token = generate_token()
    activation_key = ActivationKey(
        token_hash=hash_token(token),
        duration=data.duration,
        origin_code=data.origin_code,
        tg_user_id=data.tg_user_id,
    )
    try:
        activation_key_repo.insert(session, activation_key)
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("token collision — retry") from exc
    return activation_key, token


def get_activation_key(session: Session, key_id: UUID) -> ActivationKey:
    activation_key = activation_key_repo.get_by_id(session, key_id)
    if activation_key is None:
        raise NotFoundError(f"activation_key {key_id} not found")
    return activation_key


def list_activation_keys(
    session: Session, cursor: str | None, limit: int
) -> tuple[list[ActivationKey], str | None]:
    limit = max(1, min(limit, MAX_LIMIT))
    after = decode_cursor(cursor) if cursor else None

    rows = activation_key_repo.list_page(session, after, limit + 1)

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at, last.id)

    return rows, next_cursor


def update_activation_key(session: Session, key_id: UUID, data: ActivationKeyUpdate) -> ActivationKey:
    activation_key = get_activation_key(session, key_id)
    for field in data.model_fields_set:
        setattr(activation_key, field, getattr(data, field))
    session.commit()
    session.refresh(activation_key)
    return activation_key


def delete_activation_key(session: Session, key_id: UUID) -> None:
    activation_key = get_activation_key(session, key_id)
    if activation_key.status_code != "issued":
        raise NotDeletableError(
            f"activation_key {key_id} has status={activation_key.status_code!r}, only 'issued' keys can be deleted"
        )
    activation_key_repo.delete(session, activation_key)
