"""Репозиторий `account` — persistence, без бизнес-правил (те в
services/account_service.py). Шаблон — repositories/user_repo.py."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from models.account import Account


def insert(session: Session, account: Account) -> Account:
    session.add(account)
    session.flush()  # видит уникальность login до commit — коммитит вызывающая транзакция целиком
    return account


def get_by_id(session: Session, account_id: UUID) -> Account | None:
    return session.get(Account, account_id)
