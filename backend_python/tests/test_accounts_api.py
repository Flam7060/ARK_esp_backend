"""HTTP-контракт `POST /v1/accounts/register` — публичный, без токена."""

from __future__ import annotations

from datetime import timedelta

from core.tokens import generate_token, hash_token
from models.activation_key import ActivationKey
from repositories import activation_key_repo


def _issue_key(db_session, duration: timedelta = timedelta(days=30)) -> str:
    token = generate_token()
    activation_key_repo.insert(db_session, ActivationKey(token_hash=hash_token(token), duration=duration))
    return token


def test_register_with_valid_key_returns_201(client, db_session):
    token = _issue_key(db_session)

    resp = client.post(
        "/v1/accounts/register",
        json={"login": "newplayer", "password": "correct horse battery", "activation_key": token},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["login"] == "newplayer"
    assert body["status_code"] == "active"
    assert body["expires_at"] is not None
    assert "password" not in body
    assert "password_hash" not in body


def test_register_with_unknown_key_returns_422(client):
    resp = client.post(
        "/v1/accounts/register",
        json={"login": "ghost", "password": "correct horse battery", "activation_key": "garbage-token"},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_activation_key"


def test_register_with_already_used_key_returns_422(client, db_session):
    token = _issue_key(db_session)
    first = client.post(
        "/v1/accounts/register",
        json={"login": "first_reg", "password": "correct horse battery", "activation_key": token},
    )
    assert first.status_code == 201

    resp = client.post(
        "/v1/accounts/register",
        json={"login": "second_reg", "password": "correct horse battery", "activation_key": token},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "invalid_activation_key"


def test_register_with_taken_login_returns_409(client, db_session):
    client.post(
        "/v1/accounts/register",
        json={"login": "taken_login", "password": "correct horse battery", "activation_key": _issue_key(db_session)},
    )

    resp = client.post(
        "/v1/accounts/register",
        json={"login": "taken_login", "password": "correct horse battery", "activation_key": _issue_key(db_session)},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "login_taken"


def test_register_rejects_short_password(client, db_session):
    resp = client.post(
        "/v1/accounts/register",
        json={"login": "shortpw", "password": "short", "activation_key": _issue_key(db_session)},
    )

    assert resp.status_code == 422
