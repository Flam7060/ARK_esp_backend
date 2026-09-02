"""Тесты services/password_reset_service.py + HTTP-контракт обеих ручек
(admin issue, public confirm). Ядро правила: токен гасится ровно один
раз, просроченный/чужой/погашенный токен не проходит, admin никогда не
задаёт сам пароль."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.account_auth import create_account_token
from core.admin_auth import create_admin_token
from core.passwords import hash_password, verify_password
from core.tokens import generate_token, hash_token
from models.account import Account
from models.account_password_reset_token import AccountPasswordResetToken
from models.admin import Admin
from repositories import password_reset_repo
from services.password_reset_service import (
    AccountNotFoundError,
    InvalidResetTokenError,
    confirm_reset,
    issue_reset_token,
)

OLD_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "new correct horse battery"


def _make_account(session, login: str = "resetme") -> Account:
    account = Account(login=login, password_hash=hash_password(OLD_PASSWORD))
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def _make_admin(session, username: str = "reset_admin") -> Admin:
    admin = Admin(username=username, password_hash=hash_password("adminpass123"), role_code="admin")
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


## --- service layer ---


def test_issue_reset_token_returns_plaintext_and_hashes_in_db(db_session):
    account = _make_account(db_session)
    admin = _make_admin(db_session)

    row, token = issue_reset_token(db_session, account.id, admin.id)

    assert row.account_id == account.id
    assert row.created_by_admin_id == admin.id
    assert row.used_at is None
    assert token not in row.token_hash


def test_issue_reset_token_missing_account_raises(db_session):
    admin = _make_admin(db_session, "reset_admin2")

    with pytest.raises(AccountNotFoundError):
        issue_reset_token(db_session, uuid.uuid4(), admin.id)


def test_confirm_reset_sets_new_password_and_burns_token(db_session):
    account = _make_account(db_session, "resetme2")
    admin = _make_admin(db_session, "reset_admin3")
    _, token = issue_reset_token(db_session, account.id, admin.id)

    account_id = confirm_reset(db_session, token, NEW_PASSWORD)

    assert account_id == account.id
    db_session.refresh(account)
    assert verify_password(NEW_PASSWORD, account.password_hash)
    assert not verify_password(OLD_PASSWORD, account.password_hash)


def test_confirm_reset_rejects_reused_token(db_session):
    account = _make_account(db_session, "resetme3")
    admin = _make_admin(db_session, "reset_admin4")
    _, token = issue_reset_token(db_session, account.id, admin.id)

    confirm_reset(db_session, token, NEW_PASSWORD)

    with pytest.raises(InvalidResetTokenError):
        confirm_reset(db_session, token, "another new password")


def test_confirm_reset_rejects_unknown_token(db_session):
    with pytest.raises(InvalidResetTokenError):
        confirm_reset(db_session, "not-a-real-token", NEW_PASSWORD)


def test_confirm_reset_rejects_expired_token(db_session):
    account = _make_account(db_session, "resetme4")
    admin = _make_admin(db_session, "reset_admin5")

    token = generate_token()
    row = AccountPasswordResetToken(
        account_id=account.id,
        token_hash=hash_token(token),
        created_by_admin_id=admin.id,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),  # уже просрочен
    )
    password_reset_repo.insert(db_session, row)

    with pytest.raises(InvalidResetTokenError):
        confirm_reset(db_session, token, NEW_PASSWORD)


## --- HTTP layer ---


def test_admin_issue_reset_token_requires_admin_token(client, db_session):
    account = _make_account(db_session, "http_reset1")

    resp = client.post(f"/v1/admin/accounts/{account.id}/password-reset-tokens")

    assert resp.status_code == 401


def test_admin_issue_reset_token_returns_token_once(client, db_session):
    account = _make_account(db_session, "http_reset2")
    admin = _make_admin(db_session, "http_reset_admin")
    admin_token = create_admin_token(admin)

    resp = client.post(
        f"/v1/admin/accounts/{account.id}/password-reset-tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["account_id"] == str(account.id)
    assert len(body["token"]) > 20


def test_confirm_reset_api_full_flow(client, db_session):
    account = _make_account(db_session, "http_reset3")
    admin = _make_admin(db_session, "http_reset_admin2")
    admin_token = create_admin_token(admin)

    issue_resp = client.post(
        f"/v1/admin/accounts/{account.id}/password-reset-tokens",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    reset_token = issue_resp.json()["token"]

    confirm_resp = client.post(
        "/v1/accounts/password-reset/confirm", json={"token": reset_token, "new_password": NEW_PASSWORD}
    )
    assert confirm_resp.status_code == 204

    login_resp = client.post("/v1/accounts/auth/login", json={"login": "http_reset3", "password": NEW_PASSWORD})
    assert login_resp.status_code == 200


def test_confirm_reset_api_rejects_garbage_token(client):
    resp = client.post(
        "/v1/accounts/password-reset/confirm", json={"token": "garbage", "new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_reset_token"


def test_account_token_cannot_be_used_as_admin_token(client, db_session):
    """AccountClaims и AdminClaims — разные JWT-неймспейсы: токен
    account'а не должен проходить на admin-only ручку."""
    account = _make_account(db_session, "not_an_admin")
    account_token = create_account_token(account)

    resp = client.post(
        f"/v1/admin/accounts/{account.id}/password-reset-tokens",
        headers={"Authorization": f"Bearer {account_token}"},
    )

    assert resp.status_code == 401
