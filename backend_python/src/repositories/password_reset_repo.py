"""Репозиторий `account_password_reset_token` — persistence, шаблон
repositories/activation_key_repo.py (тот же FOR UPDATE паттерн: токен
гасится ровно один раз, гонка закрывается блокировкой строки в БД)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.account_password_reset_token import AccountPasswordResetToken


def insert(session: Session, token_row: AccountPasswordResetToken) -> AccountPasswordResetToken:
    session.add(token_row)
    session.commit()
    session.refresh(token_row)
    return token_row


def get_by_token_hash_for_update(session: Session, token_hash: str) -> AccountPasswordResetToken | None:
    stmt = (
        select(AccountPasswordResetToken)
        .where(AccountPasswordResetToken.token_hash == token_hash)
        .with_for_update()
    )
    return session.execute(stmt).scalar_one_or_none()
