"""Аутентификация для `account` (обычный пользователь) — база, без которой
ни смена пароля, ни API-ключи, ни группы не имеют субъекта действия: всем
им нужен "кто сейчас залогинен".

Блокировка после порога неудач через `failed_attempts`/`locked_until` —
зеркало core/admin_auth.py, тот же контракт, теперь и на models/account.py.
Алгоритм подписи — НЕ зеркало admin: admin-токены остаются HS256 на
`APP_SECRET_KEY` (админы никогда не ходят в ark_relay), а account-токены
подписаны RS256 той же RSA-парой (`config.jwt.*_KEY_PATH`), что
`internal/authjwt` в Go-сервисе `ark_relay` уже проверяет офлайн —
единственный live-канал шеринга (WS/QUIC) принимает только RS256 с этим
публичным ключом, так что account-токен обязан быть подписан им, а не
общим HS256-секретом. `AccountClaims`/`AdminClaims` остаются разными
субъектами; разница в алгоритме и ключе делает их невзаимозаменяемыми ещё
надёжнее, чем раньше делал один только claim `typ` (admin-токен на RS256
даже не пройдёт проверку подписи, не дойдёт до сравнения `typ`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import config
from core.db import get_session
from core.passwords import hash_password, verify_password
from core.tokens import hash_token
from models.account import Account
from repositories import api_key_repo


def _resolve_key_path(configured: str) -> Path:
    path = Path(configured)
    if path.is_absolute():
        return path
    # .env хранит путь относительно Backend/ -- тот же каталог, что
    # ENV_FILE в core/config.py (см. core/security.py::_public_key,
    # тот же приём для проверки другого, старого HTTP-телеметрии контракта).
    from core.config import ENV_FILE

    return (ENV_FILE.parent / path).resolve()


@lru_cache(maxsize=1)
def _account_token_private_key() -> str:
    path = _resolve_key_path(config.jwt.PRIVATE_KEY_PATH)
    try:
        return path.read_text()
    except OSError as exc:
        raise RuntimeError(f"account_auth: не удалось прочитать приватный ключ {path}: {exc}") from exc


@lru_cache(maxsize=1)
def _account_token_public_key() -> str:
    path = _resolve_key_path(config.jwt.PUBLIC_KEY_PATH)
    try:
        return path.read_text()
    except OSError as exc:
        raise RuntimeError(f"account_auth: не удалось прочитать публичный ключ {path}: {exc}") from exc

FAILED_ATTEMPTS_LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION = timedelta(minutes=15)

_bearer = HTTPBearer(auto_error=False)
_DUMMY_PASSWORD_HASH = hash_password("timing-attack-mitigation-constant")


class AccountClaims(BaseModel):
    """Поля читаны из БД в момент запроса, не из самого JWT — блокировка
    account'а (status_code/locked_until) действует немедленно."""

    account_id: uuid.UUID
    login: str


class InvalidCredentialsError(Exception):
    """Неверный логин/пароль — один класс на оба случая (не подсказка
    атакующему, какие login вообще существуют)."""


class AccountLockedError(Exception):
    """Учётка временно заблокирована после серии неудачных попыток."""

    def __init__(self, locked_until: datetime) -> None:
        self.locked_until = locked_until
        super().__init__(f"locked until {locked_until.isoformat()}")


def authenticate_account(session: Session, login: str, password: str) -> Account:
    account = session.execute(
        select(Account).where(Account.login == login, Account.deleted_at.is_(None))
    ).scalar_one_or_none()
    if account is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise InvalidCredentialsError("invalid login or password")

    now = datetime.now(UTC)
    if account.locked_until is not None and account.locked_until > now:
        raise AccountLockedError(account.locked_until)

    if account.status_code != "active":
        raise InvalidCredentialsError(f"account status is {account.status_code!r}, not active")

    if not verify_password(password, account.password_hash):
        account.failed_attempts += 1
        if account.failed_attempts >= FAILED_ATTEMPTS_LOCKOUT_THRESHOLD:
            account.locked_until = now + LOCKOUT_DURATION
        session.commit()
        raise InvalidCredentialsError("invalid login or password")

    account.failed_attempts = 0
    account.locked_until = None
    account.last_login_at = now
    session.commit()
    session.refresh(account)
    return account


def create_account_token(account: Account) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(account.id),
        # "typ" — исторически отличало account- от admin-токена, когда оба
        # были на одном HS256-секрете; алгоритм/ключ ниже уже делают это
        # сами, поле оставлено для внутренней читаемости, не как единственная
        # линия обороны.
        "typ": "account",
        "login": account.login,
        # account_id -- claim, который реально проверяет ark_relay
        # (internal/authjwt.Claims в Go): единственное, что ему нужно от
        # токена (см. этот модуль's docstring). Дублирует "sub" по значению
        # -- Python decode ниже продолжает читать "sub", Go читает
        # "account_id"; оба должны совпадать с одним и тем же account.id.
        "account_id": str(account.id),
        "iat": now,
        "exp": now + timedelta(seconds=config.app.JWT_LIFETIME),
    }
    return jwt.encode(payload, _account_token_private_key(), algorithm="RS256")


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "invalid_account_token", "message": message, "details": {}}},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _account_claims_from_api_key(session: Session, token: str) -> AccountClaims:
    """Bearer-токен, который не разобрался как JWT (`get_current_account`
    пробует его первым — короткий opaque api_key никогда не спутать с
    JWT-строкой, но обратное сообщение об ошибке не должно намекать,
    какая из двух проверок именно провалилась), проверяется как
    self-service `api_key` (routers/v1/api_keys.py) — тот же Bearer-слот,
    что и account-JWT, ключ используется на месте JWT, а не рядом с ним."""
    api_key = api_key_repo.get_active_by_hash(session, hash_token(token))
    if api_key is None:
        raise _unauthorized("недействительный токен")

    now = datetime.now(UTC)
    if api_key.expires_at is not None and api_key.expires_at <= now:
        raise _unauthorized("недействительный токен")

    account = session.get(Account, api_key.account_id)
    if account is None or account.deleted_at is not None or account.status_code != "active":
        raise _unauthorized("account не найден или не активен")

    api_key.last_used_at = now
    session.commit()

    return AccountClaims(account_id=account.id, login=account.login)


async def get_current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> AccountClaims:
    if credentials is None:
        raise _unauthorized("Authorization: Bearer <token> обязателен")

    try:
        payload = jwt.decode(credentials.credentials, _account_token_public_key(), algorithms=["RS256"])
    except jwt.PyJWTError:
        # Не JWT (или не тем ключом подписан) -- второй шанс: тот же
        # Bearer-слот принимает опаковый api_key ВМЕСТО JWT, когда клиенту
        # нечем предъявить JWT (см. docstring _account_claims_from_api_key).
        return _account_claims_from_api_key(session, credentials.credentials)

    if payload.get("typ") != "account":
        raise _unauthorized("токен не account-типа")

    try:
        account_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _unauthorized("токен не содержит валидный sub") from exc

    account = session.get(Account, account_id)
    if account is None or account.deleted_at is not None or account.status_code != "active":
        raise _unauthorized("account не найден или не активен")

    return AccountClaims(account_id=account.id, login=account.login)
