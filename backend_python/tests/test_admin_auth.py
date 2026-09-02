"""Тесты core/admin_auth.py — TDD для аутентификации, которой раньше не
существовало вообще (models/admin.py заводит учётку, но никто не проверял
пароль на вход). Покрывает: успешный логин, неверный пароль/username,
блокировку после порога неудач (failed_attempts/locked_until — колонки,
которые до этого модуля никто не читал и не писал), отключённую учётку.
"""

from __future__ import annotations

import jwt
import pytest

from core.admin_auth import (
    FAILED_ATTEMPTS_LOCKOUT_THRESHOLD,
    AccountLockedError,
    InvalidCredentialsError,
    authenticate_admin,
    create_admin_token,
)
from core.config import config
from core.passwords import hash_password
from models.admin import Admin

PASSWORD = "correct horse battery staple"


def _make_admin(session, username: str, role_code: str = "admin", status_code: str = "active") -> Admin:
    # admin_role/admin_status уже засеяны conftest.py::_seed_code_lookups —
    # здесь только сама учётка.
    admin = Admin(username=username, password_hash=hash_password(PASSWORD), role_code=role_code, status_code=status_code)
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def test_authenticate_admin_accepts_correct_password(db_session):
    admin = _make_admin(db_session, "root")

    result = authenticate_admin(db_session, "root", PASSWORD)

    assert result.id == admin.id
    assert result.failed_attempts == 0
    assert result.last_login_at is not None


def test_authenticate_admin_rejects_wrong_password(db_session):
    _make_admin(db_session, "root2")

    with pytest.raises(InvalidCredentialsError):
        authenticate_admin(db_session, "root2", "wrong password")


def test_authenticate_admin_rejects_unknown_username(db_session):
    with pytest.raises(InvalidCredentialsError):
        authenticate_admin(db_session, "nobody-at-all", "whatever")


def test_authenticate_admin_increments_failed_attempts(db_session):
    admin = _make_admin(db_session, "root3")

    with pytest.raises(InvalidCredentialsError):
        authenticate_admin(db_session, "root3", "wrong password")

    db_session.refresh(admin)
    assert admin.failed_attempts == 1
    assert admin.locked_until is None


def test_authenticate_admin_locks_after_threshold_and_then_rejects_correct_password(db_session):
    admin = _make_admin(db_session, "root4")

    for _ in range(FAILED_ATTEMPTS_LOCKOUT_THRESHOLD):
        with pytest.raises(InvalidCredentialsError):
            authenticate_admin(db_session, "root4", "wrong password")

    db_session.refresh(admin)
    assert admin.locked_until is not None

    # Даже с ПРАВИЛЬНЫМ паролем — залочено, это временная блокировка,
    # а не "ещё одна попытка сбросит счётчик".
    with pytest.raises(AccountLockedError):
        authenticate_admin(db_session, "root4", PASSWORD)


def test_authenticate_admin_rejects_disabled_status(db_session):
    _make_admin(db_session, "disabled_user", status_code="disabled")

    with pytest.raises(InvalidCredentialsError):
        authenticate_admin(db_session, "disabled_user", PASSWORD)


def test_create_admin_token_roundtrips_claims(db_session):
    admin = _make_admin(db_session, "tokentest")

    token = create_admin_token(admin)
    payload = jwt.decode(token, config.app.SECRET_KEY.get_secret_value(), algorithms=["HS256"])

    assert payload["sub"] == str(admin.id)
    assert payload["username"] == "tokentest"
    assert payload["role"] == "admin"
