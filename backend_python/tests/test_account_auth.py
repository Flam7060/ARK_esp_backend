"""Тесты core/account_auth.py — зеркало test_admin_auth.py, для account
вместо admin (тот же лочаут-контракт, теперь и на Account.failed_attempts
/locked_until)."""

from __future__ import annotations

import jwt
import pytest

from core.account_auth import (
    FAILED_ATTEMPTS_LOCKOUT_THRESHOLD,
    AccountLockedError,
    InvalidCredentialsError,
    _account_token_public_key,
    authenticate_account,
    create_account_token,
)
from core.passwords import hash_password
from models.account import Account

PASSWORD = "correct horse battery staple"


def _make_account(session, login: str, status_code: str = "active") -> Account:
    account = Account(login=login, password_hash=hash_password(PASSWORD), status_code=status_code)
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def test_authenticate_account_accepts_correct_password(db_session):
    account = _make_account(db_session, "player1")

    result = authenticate_account(db_session, "player1", PASSWORD)

    assert result.id == account.id
    assert result.failed_attempts == 0
    assert result.last_login_at is not None


def test_authenticate_account_rejects_wrong_password(db_session):
    _make_account(db_session, "player2")

    with pytest.raises(InvalidCredentialsError):
        authenticate_account(db_session, "player2", "wrong password")


def test_authenticate_account_rejects_unknown_login(db_session):
    with pytest.raises(InvalidCredentialsError):
        authenticate_account(db_session, "nobody-at-all", "whatever")


def test_authenticate_account_locks_after_threshold(db_session):
    account = _make_account(db_session, "player3")

    for _ in range(FAILED_ATTEMPTS_LOCKOUT_THRESHOLD):
        with pytest.raises(InvalidCredentialsError):
            authenticate_account(db_session, "player3", "wrong password")

    db_session.refresh(account)
    assert account.locked_until is not None

    with pytest.raises(AccountLockedError):
        authenticate_account(db_session, "player3", PASSWORD)


def test_authenticate_account_rejects_suspended_status(db_session):
    _make_account(db_session, "player4", status_code="suspended")

    with pytest.raises(InvalidCredentialsError):
        authenticate_account(db_session, "player4", PASSWORD)


def test_authenticate_account_ignores_soft_deleted(db_session):
    account = _make_account(db_session, "player5")
    account.deleted_at = account.created_at
    db_session.commit()

    with pytest.raises(InvalidCredentialsError):
        authenticate_account(db_session, "player5", PASSWORD)


def test_create_account_token_roundtrips_claims(db_session):
    account = _make_account(db_session, "player6")

    token = create_account_token(account)
    payload = jwt.decode(token, _account_token_public_key(), algorithms=["RS256"])

    assert payload["sub"] == str(account.id)
    assert payload["login"] == "player6"


def test_create_account_token_carries_account_id_for_ark_relay(db_session):
    """ark_relay (backend_go's internal/authjwt.Claims) trusts only this
    one claim -- see account_auth.py's module docstring."""
    account = _make_account(db_session, "player7")

    token = create_account_token(account)
    payload = jwt.decode(token, _account_token_public_key(), algorithms=["RS256"])

    assert payload["account_id"] == str(account.id)
