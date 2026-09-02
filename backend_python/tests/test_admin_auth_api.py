"""HTTP-контракт `/v1/admin/auth/login` — шаблон test_users_api.py."""

from __future__ import annotations

from core.passwords import hash_password
from models.admin import Admin

PASSWORD = "correct horse battery staple"


def _make_admin(session, username: str = "root") -> Admin:
    admin = Admin(username=username, password_hash=hash_password(PASSWORD), role_code="admin")
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def test_login_with_correct_password_returns_token(client, db_session):
    _make_admin(db_session, "login_ok")

    resp = client.post("/v1/admin/auth/login", json={"username": "login_ok", "password": PASSWORD})

    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) > 20
    assert body["expires_in"] > 0


def test_login_with_wrong_password_returns_401(client, db_session):
    _make_admin(db_session, "login_bad")

    resp = client.post("/v1/admin/auth/login", json={"username": "login_bad", "password": "nope"})

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "invalid_credentials"


def test_login_with_unknown_username_returns_401(client):
    resp = client.post("/v1/admin/auth/login", json={"username": "no-such-admin", "password": "whatever"})

    assert resp.status_code == 401
