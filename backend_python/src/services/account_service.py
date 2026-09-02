"""Сервис регистрации `account` — единственное место, где `activation_key`
реально гасится (issued -> redeemed). Один commit на всю операцию: строка
ключа блокируется `FOR UPDATE` до конца транзакции, поэтому либо И аккаунт
создаётся, И ключ гасится, либо не происходит ни то ни другое — не бывает
состояния "ключ уже redeemed, а аккаунта нет" или наоборот.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.passwords import hash_password, verify_password
from core.tokens import hash_token
from models.account import Account
from repositories import account_repo, activation_key_repo
from routers.v1.schemas.account import AccountRegisterRequest, ChangePasswordRequest

__all__ = [
    "ConflictError",
    "InvalidActivationKeyError",
    "InvalidOldPasswordError",
    "change_password",
    "register_account",
]


class InvalidActivationKeyError(Exception):
    """Ключа с таким токеном нет, либо он уже погашен — оба случая
    одна и та же ошибка наружу (§ безопасность): "неверный или уже
    использованный ключ" не даёт перебором отличить "ключа не существует"
    от "ключ есть, но занят"."""


class ConflictError(Exception):
    """login уже занят активным (не удалённым) аккаунтом."""


class InvalidOldPasswordError(Exception):
    """Текущий пароль не совпал — не даём сменить пароль по одному только
    валидному JWT: JWT доказывает "кто-то вошёл раньше", а не "это точно
    хозяин пароля прямо сейчас за клавиатурой" (украденный, но ещё живой
    токен не должен давать взять аккаунт полностью)."""


def register_account(session: Session, data: AccountRegisterRequest) -> Account:
    token_hash = hash_token(data.activation_key)
    key = activation_key_repo.get_by_token_hash_for_update(session, token_hash)
    if key is None or key.status_code != "issued":
        # Ключ существует, но уже используется другим запросом ПРЯМО
        # СЕЙЧАС (FOR UPDATE держит блокировку) — второй запрос ждёт на
        # локе и после разблокировки увидит status_code='redeemed', т.е.
        # тоже упадёт сюда. Race закрыт на уровне БД, не приложения.
        raise InvalidActivationKeyError("ключ активации не найден или уже использован")

    now = datetime.now(UTC)
    account = Account(
        login=data.login,
        password_hash=hash_password(data.password),
        expires_at=now + key.duration,
    )
    try:
        account_repo.insert(session, account)
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(f"login={data.login!r} уже занят") from exc

    key.status_code = "redeemed"
    key.redeemed_at = now
    key.redeemed_by_account_id = account.id

    session.commit()
    session.refresh(account)
    return account


def change_password(session: Session, account_id: uuid.UUID, data: ChangePasswordRequest) -> Account:
    """Смена пароля залогиненным пользователем — FR-053. Не трогает
    failed_attempts/locked_until: это состояние про логин-форму, а не
    про эту ручку (сюда не попадёшь без валидного JWT, значит блокировка
    по неверным попыткам логина уже не актуальна)."""
    account = account_repo.get_by_id(session, account_id)
    if account is None:
        # get_current_account уже гарантирует существование/активность —
        # сюда попасть можно только если account удалили МЕЖДУ проверкой
        # токена и этим вызовом; не бизнес-кейс, а гонка.
        raise InvalidOldPasswordError("account not found")

    if not verify_password(data.old_password, account.password_hash):
        raise InvalidOldPasswordError("old password does not match")

    account.password_hash = hash_password(data.new_password)
    session.commit()
    session.refresh(account)
    return account
