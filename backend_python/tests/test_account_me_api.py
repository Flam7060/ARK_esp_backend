"""HTTP-контракт `GET /v1/accounts/me`."""

from __future__ import annotations

from core.account_auth import create_account_token
from core.passwords import hash_password
from models.account import Account

PASSWORD = "correct horse battery staple"


def _make_account(session, login: str = "me_tester") -> Account:
    account = Account(login=login, password_hash=hash_password(PASSWORD))
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def test_get_me_requires_token(client):
    resp = client.get("/v1/accounts/me")
    assert resp.status_code == 401


def test_get_me_returns_id_and_empty_groups_for_fresh_account(client, db_session):
    account = _make_account(db_session, "me_fresh")
    token = create_account_token(account)

    resp = client.get("/v1/accounts/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(account.id)
    assert body["groups"] == []


def test_get_me_lists_groups_the_account_belongs_to(client, db_session):
    account = _make_account(db_session, "me_grouped")
    token = create_account_token(account)
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post("/v1/groups", json={"name": "my-squad"}, headers=headers)
    assert created.status_code == 201
    group_id = created.json()["id"]

    resp = client.get("/v1/accounts/me", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(account.id)
    assert [g["id"] for g in body["groups"]] == [group_id]
    assert body["groups"][0]["name"] == "my-squad"
