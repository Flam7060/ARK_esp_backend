"""Аутентификация для `admin` (внутренняя админка) — раньше не
существовала вообще: models/admin.py заводит учётку только через CLI
(scripts/create_admin.py), но ничего не проверяло пароль на вход и не
выдавало токен для последующих запросов.

Отдельно от core/security.py намеренно: тот модуль — телеметрийный JWT
(RS256, внешний издатель, публичный ключ шарится с ark_relay/backend_go).
Здесь — HS256, издаём и проверяем сами, симметричным ключом (тем же
APP_SECRET_KEY, что FastAPI уже использует для себя) — незачем городить
второй асимметричный ключ ради токена, который никогда не покидает этот
процесс.

Блокировка после подряд неудачных попыток — не отдельная фича, а то, для
чего в модели уже есть `failed_attempts`/`locked_until` (см. models/admin.py):
эти колонки лежали неиспользуемыми до этого модуля.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import config
from core.db import get_session
from core.passwords import hash_password, verify_password
from models.admin import Admin

# После этого числа подряд неверных паролей учётка временно блокируется —
# без этого порога password-эндпоинт превращается в оракул для перебора
# со скоростью сети, а не скоростью argon2.
FAILED_ATTEMPTS_LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION = timedelta(minutes=15)

_bearer = HTTPBearer(auto_error=False)

# Настоящий argon2-хеш (не строка, собранная руками) — verify_password на
# нём реально прогоняет argon2, а не падает на InvalidHashError раньше
# срока. Нужен только затем, чтобы ветка "username не найден" стоила
# столько же по времени, сколько "username найден, пароль неверный" —
# иначе разница в задержке сама по себе выдаёт, какие username существуют.
_DUMMY_PASSWORD_HASH = hash_password("timing-attack-mitigation-constant")


class AdminClaims(BaseModel):
    """Итог успешной проверки токена — поля читаны из БД в момент запроса
    (не из самого JWT), поэтому смена role_code/status_code у админа
    применяется немедленно, не дожидаясь истечения токена."""

    admin_id: uuid.UUID
    username: str
    role_code: str


class InvalidCredentialsError(Exception):
    """Неверный логин/пароль — намеренно один класс на оба случая
    (см. authenticate_admin): различать их наружу — подсказка атакующему,
    какие username вообще существуют."""


class AccountLockedError(Exception):
    """Учётка временно заблокирована после серии неудачных попыток."""

    def __init__(self, locked_until: datetime) -> None:
        self.locked_until = locked_until
        super().__init__(f"locked until {locked_until.isoformat()}")


def authenticate_admin(session: Session, username: str, password: str) -> Admin:
    """Полный цикл логина: статус/блокировка -> пароль -> сброс/накрутка
    счётчика неудач -> last_login_at. Один коммит на попытку — состояние
    счётчика переживает процесс, а не сбрасывается при рестарте."""
    admin = session.execute(select(Admin).where(Admin.username == username)).scalar_one_or_none()
    if admin is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise InvalidCredentialsError("invalid username or password")

    now = datetime.now(UTC)
    if admin.locked_until is not None and admin.locked_until > now:
        raise AccountLockedError(admin.locked_until)

    if admin.status_code != "active":
        raise InvalidCredentialsError(f"admin status is {admin.status_code!r}, not active")

    if not verify_password(password, admin.password_hash):
        admin.failed_attempts += 1
        if admin.failed_attempts >= FAILED_ATTEMPTS_LOCKOUT_THRESHOLD:
            admin.locked_until = now + LOCKOUT_DURATION
        session.commit()
        raise InvalidCredentialsError("invalid username or password")

    admin.failed_attempts = 0
    admin.locked_until = None
    admin.last_login_at = now
    session.commit()
    session.refresh(admin)
    return admin


def create_admin_token(admin: Admin) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin.id),
        # "typ" — не декоративное поле: admin-токен и account-токен
        # (core/account_auth.py) подписаны ОДНИМ секретом (APP_SECRET_KEY),
        # без этого поля токен account'а прошёл бы здесь при случайном
        # совпадении account.id == admin.id (астрономически маловероятно,
        # но не невозможно — UUID4 не гарантирует непересечение между
        # разными таблицами формально, только вероятностно).
        "typ": "admin",
        "username": admin.username,
        "role": admin.role_code,
        "iat": now,
        "exp": now + timedelta(seconds=config.app.JWT_LIFETIME),
    }
    return jwt.encode(payload, config.app.SECRET_KEY.get_secret_value(), algorithm="HS256")


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "invalid_admin_token", "message": message, "details": {}}},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
) -> AdminClaims:
    """FastAPI-зависимость для admin-only роутеров. Проверяет подпись/exp
    токена, ЗАТЕМ перечитывает admin из БД — токен доказывает "кто-то
    залогинился недавно", а не "эта роль/статус ещё актуальны"."""
    if credentials is None:
        raise _unauthorized("Authorization: Bearer <token> обязателен")

    try:
        payload = jwt.decode(credentials.credentials, config.app.SECRET_KEY.get_secret_value(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise _unauthorized(str(exc)) from exc

    if payload.get("typ") != "admin":
        raise _unauthorized("токен не admin-типа")

    try:
        admin_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _unauthorized("токен не содержит валидный sub") from exc

    admin = session.get(Admin, admin_id)
    if admin is None or admin.status_code != "active":
        raise _unauthorized("admin не найден или не активен")

    return AdminClaims(admin_id=admin.id, username=admin.username, role_code=admin.role_code)
