"""HTTP-контракт `/v1/accounts/me/api-keys`."""

from __future__ import annotations

from core.account_auth import create_account_token
from core.passwords import hash_password
from models.account import Account


def _account_and_token(session, login: str) -> str:
    account = Account(login=login, password_hash=hash_password("correct horse battery"))
    session.add(account)
    session.commit()
    return create_account_token(account)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_endpoints_reject_missing_token(client):
    assert client.post("/v1/accounts/me/api-keys", json={}).status_code == 401
    assert client.get("/v1/accounts/me/api-keys").status_code == 401


def test_create_and_list_and_revoke_api_key(client, db_session):
    token = _account_and_token(db_session, "apikey_user1")

    created = client.post("/v1/accounts/me/api-keys", json={"scopes": ["telemetry:write"]}, headers=_auth(token))
    assert created.status_code == 201
    body = created.json()
    assert body["scopes"] == ["telemetry:write"]
    assert len(body["token"]) > 20

    listed = client.get("/v1/accounts/me/api-keys", headers=_auth(token))
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["token"] is None  # список не показывает плейнтекст повторно

    revoked = client.delete(f"/v1/accounts/me/api-keys/{body['id']}", headers=_auth(token))
    assert revoked.status_code == 200
    assert revoked.json()["status_code"] == "revoked"


def test_api_key_isolation_between_accounts(client, db_session):
    token_a = _account_and_token(db_session, "apikey_user2")
    token_b = _account_and_token(db_session, "apikey_user3")

    created = client.post("/v1/accounts/me/api-keys", json={}, headers=_auth(token_a)).json()

    resp = client.get(f"/v1/accounts/me/api-keys/{created['id']}", headers=_auth(token_b))

    assert resp.status_code == 404
