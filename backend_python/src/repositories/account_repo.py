"""Репозиторий `account` — persistence, без бизнес-правил (те в
services/account_service.py). Шаблон — repositories/user_repo.py."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.account import Account


def insert(session: Session, account: Account) -> Account:
    session.add(account)
    session.flush()  # видит уникальность login до commit — коммитит вызывающая транзакция целиком
    return account


def get_by_id(session: Session, account_id: UUID) -> Account | None:
    return session.get(Account, account_id)


def list_ids_with_active_group(session: Session, group_id: UUID) -> list[UUID]:
    """Аккаунты, у которых ИМЕННО эта группа сейчас активна для шеринга —
    не то же самое, что "все участники группы" (participant может состоять
    в группе, но иметь активной другую — см. services.sharing_service
    .set_active_group). Нужен delete_group'у, чтобы снести кэш активной
    группы только у тех, у кого он реально на неё указывает."""
    stmt = select(Account.id).where(Account.active_group_id == group_id)
    return list(session.execute(stmt).scalars())
