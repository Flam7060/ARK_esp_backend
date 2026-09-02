"""Тесты services/account_service.change_password + HTTP-контракт
`/v1/accounts/me/change-password`."""

from __future__ import annotations

import uuid

import pytest

from core.account_auth import create_account_token
from core.passwords import hash_password, verify_password
from models.account import Account
from routers.v1.schemas.account import ChangePasswordRequest
from services.account_service import InvalidOldPasswordError, change_password

OLD_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "new correct horse battery"


def _make_account(session, login: str = "changer") -> Account:
    account = Account(login=login, password_hash=hash_password(OLD_PASSWORD))
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def test_change_password_service_accepts_correct_old_password(db_session):
    account = _make_account(db_session)

    updated = change_password(
        db_session, account.id, ChangePasswordRequest(old_password=OLD_PASSWORD, new_password=NEW_PASSWORD)
    )

    assert verify_password(NEW_PASSWORD, updated.password_hash)
    assert not verify_password(OLD_PASSWORD, updated.password_hash)


def test_change_password_service_rejects_wrong_old_password(db_session):
    account = _make_account(db_session)

    with pytest.raises(InvalidOldPasswordError):
        change_password(
            db_session, account.id, ChangePasswordRequest(old_password="wrong", new_password=NEW_PASSWORD)
        )


def test_change_password_service_rejects_missing_account(db_session):
    with pytest.raises(InvalidOldPasswordError):
        change_password(
            db_session, uuid.uuid4(), ChangePasswordRequest(old_password=OLD_PASSWORD, new_password=NEW_PASSWORD)
        )


def test_change_password_api_requires_token(client):
    resp = client.post(
        "/v1/accounts/me/change-password", json={"old_password": OLD_PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 401


def test_change_password_api_with_valid_token_and_old_password(client, db_session):
    account = _make_account(db_session, "api_changer")
    token = create_account_token(account)

    resp = client.post(
        "/v1/accounts/me/change-password",
        json={"old_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200

    # Новый пароль реально применился — логин со старым больше не проходит.
    login_old = client.post("/v1/accounts/auth/login", json={"login": "api_changer", "password": OLD_PASSWORD})
    assert login_old.status_code == 401
    login_new = client.post("/v1/accounts/auth/login", json={"login": "api_changer", "password": NEW_PASSWORD})
    assert login_new.status_code == 200


def test_change_password_api_rejects_wrong_old_password(client, db_session):
    account = _make_account(db_session, "api_changer2")
    token = create_account_token(account)

    resp = client.post(
        "/v1/accounts/me/change-password",
        json={"old_password": "wrong", "new_password": NEW_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "invalid_old_password"


def test_change_password_api_rejects_short_new_password(client, db_session):
    account = _make_account(db_session, "api_changer3")
    token = create_account_token(account)

    resp = client.post(
        "/v1/accounts/me/change-password",
        json={"old_password": OLD_PASSWORD, "new_password": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
