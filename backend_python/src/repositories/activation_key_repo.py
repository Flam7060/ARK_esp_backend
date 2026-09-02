"""Репозиторий `activation_key` — persistence, без бизнес-правил (те в
services/activation_key_service.py). Шаблон — repositories/user_repo.py."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.activation_key import ActivationKey


def insert(session: Session, activation_key: ActivationKey) -> ActivationKey:
    session.add(activation_key)
    session.commit()  # уникальность token_hash — может бросить IntegrityError, ловит сервис
    session.refresh(activation_key)
    return activation_key


def get_by_id(session: Session, key_id: UUID) -> ActivationKey | None:
    return session.get(ActivationKey, key_id)


def get_by_token_hash_for_update(session: Session, token_hash: str) -> ActivationKey | None:
    """`FOR UPDATE` — берёт блокировку строки на время транзакции
    регистрации (см. services/account_service.register_account): без неё
    два параллельных запроса с одним и тем же ключом оба прочитали бы
    status_code='issued' до того, как второй увидит commit первого, и оба
    погасили бы один ключ дважды."""
    stmt = select(ActivationKey).where(ActivationKey.token_hash == token_hash).with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def list_page(
    session: Session, after: tuple[datetime, UUID] | None, limit: int
) -> list[ActivationKey]:
    stmt = select(ActivationKey)
    if after is not None:
        created_at, row_id = after
        stmt = stmt.where(
            (ActivationKey.created_at < created_at)
            | ((ActivationKey.created_at == created_at) & (ActivationKey.id < row_id))
        )
    stmt = stmt.order_by(ActivationKey.created_at.desc(), ActivationKey.id.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


def delete(session: Session, activation_key: ActivationKey) -> None:
    session.delete(activation_key)
    session.commit()
