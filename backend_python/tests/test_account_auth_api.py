"""HTTP-контракт `/v1/accounts/auth/login`."""

from __future__ import annotations

from core.passwords import hash_password
from models.account import Account

PASSWORD = "correct horse battery staple"


def _make_account(session, login: str = "webuser") -> Account:
    account = Account(login=login, password_hash=hash_password(PASSWORD))
    session.add(account)
    session.commit()
    return account


def test_login_with_correct_password_returns_token(client, db_session):
    _make_account(db_session, "login_ok")

    resp = client.post("/v1/accounts/auth/login", json={"login": "login_ok", "password": PASSWORD})

    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20


def test_login_with_wrong_password_returns_401(client, db_session):
    _make_account(db_session, "login_bad")

    resp = client.post("/v1/accounts/auth/login", json={"login": "login_bad", "password": "nope"})

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "invalid_credentials"
