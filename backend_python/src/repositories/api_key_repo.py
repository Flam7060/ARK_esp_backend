"""Репозиторий `api_key`/`api_key_scope` — persistence, шаблон
repositories/activation_key_repo.py. Все выборки берут `account_id` —
ключ одного account'а не должен быть виден в списке/get другого."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.api_key import ApiKey


def insert(session: Session, api_key: ApiKey) -> ApiKey:
    session.add(api_key)
    session.commit()
    session.refresh(api_key)
    return api_key


def get_active_by_hash(session: Session, key_hash: str) -> ApiKey | None:
    """Ключ для bearer-аутентификации (core/account_auth.py) — предъявленный
    токен уже свёрнут в key_hash вызывающим (core/tokens.hash_token), здесь
    только SELECT. Фильтр status_code="active" в самом запросе, а не после
    него: revoked/другой статус не должен аутентифицировать ни на секунду
    дольше, чем требуется на смену status_code в БД."""
    stmt = select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.status_code == "active")
    return session.execute(stmt).scalar_one_or_none()


def get_by_id_for_account(session: Session, key_id: UUID, account_id: UUID) -> ApiKey | None:
    stmt = select(ApiKey).where(ApiKey.id == key_id, ApiKey.account_id == account_id)
    return session.execute(stmt).scalar_one_or_none()


def list_page_for_account(
    session: Session, account_id: UUID, after: tuple[datetime, UUID] | None, limit: int
) -> list[ApiKey]:
    stmt = select(ApiKey).where(ApiKey.account_id == account_id)
    if after is not None:
        created_at, row_id = after
        stmt = stmt.where(
            (ApiKey.created_at < created_at) | ((ApiKey.created_at == created_at) & (ApiKey.id < row_id))
        )
    stmt = stmt.order_by(ApiKey.created_at.desc(), ApiKey.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())
