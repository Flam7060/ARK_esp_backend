"""Сервис сброса пароля через админа — единственный путь восстановления
(самостоятельного сброса нет: у account нет email/телефона, слать код
некуда — продуктовое решение, см. models/account_password_reset_token.py).

Админ выпускает токен (`issue_reset_token`) и передаёт его пользователю
вручную (саппорт-канал вне этого бэкенда) — сам пароль админ не видит и
не задаёт. Пользователь подтверждает токен (`confirm_reset`) и ставит
новый пароль сам. Каждое из двух действий логируется — второй, отдельный
от БД-строки слой аудита."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from core.passwords import hash_password
from core.tokens import generate_token, hash_token
from models.account_password_reset_token import AccountPasswordResetToken
from repositories import account_repo, password_reset_repo

logger = logging.getLogger(__name__)

# Короткий TTL — это не второй постоянный пароль, а разовый пропуск,
# передаваемый вручную через саппорт; час с запасом покрывает время между
# "админ выписал" и "пользователь применил", не оставляя токен валидным
# неделями, если про него забыли.
RESET_TOKEN_TTL = timedelta(hours=1)

__all__ = [
    "AccountNotFoundError",
    "InvalidResetTokenError",
    "RESET_TOKEN_TTL",
    "confirm_reset",
    "issue_reset_token",
]


class AccountNotFoundError(Exception):
    """account с таким id не существует — админ ошибся id."""


class InvalidResetTokenError(Exception):
    """Токена нет, либо просрочен, либо уже погашен — одна ошибка на все
    три случая (не подсказка о том, какие токены/аккаунты существуют)."""


def issue_reset_token(session: Session, account_id: uuid.UUID, admin_id: uuid.UUID) -> tuple[AccountPasswordResetToken, str]:
    account = account_repo.get_by_id(session, account_id)
    if account is None:
        raise AccountNotFoundError(f"account {account_id} not found")

    token = generate_token()
    now = datetime.now(UTC)
    row = AccountPasswordResetToken(
        account_id=account_id,
        token_hash=hash_token(token),
        created_by_admin_id=admin_id,
        expires_at=now + RESET_TOKEN_TTL,
    )
    password_reset_repo.insert(session, row)
    logger.info(
        "password_reset: token issued",
        extra={"account_id": str(account_id), "admin_id": str(admin_id), "reset_token_id": str(row.id)},
    )
    return row, token


def confirm_reset(session: Session, token: str, new_password: str) -> uuid.UUID:
    row = password_reset_repo.get_by_token_hash_for_update(session, hash_token(token))
    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise InvalidResetTokenError("токен сброса не найден, просрочен или уже использован")

    account = account_repo.get_by_id(session, row.account_id)
    if account is None:
        raise InvalidResetTokenError("account для этого токена не существует")

    account.password_hash = hash_password(new_password)
    # Смена пароля через восстановление — разумный момент снять и
    # блокировку по неудачным попыткам логина: новый пароль обнуляет
    # причину, по которой аккаунт мог быть заблокирован.
    account.failed_attempts = 0
    account.locked_until = None
    row.used_at = now

    session.commit()
    logger.info(
        "password_reset: token redeemed",
        extra={"account_id": str(row.account_id), "reset_token_id": str(row.id)},
    )
    return row.account_id
