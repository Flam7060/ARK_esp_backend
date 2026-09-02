"""Ad-hoc verification: Bearer <api_key> works in place of Bearer <JWT> on
get_current_account, exercising the exact routers already covered by
tests_host/test_groups_api.py and tests_host/test_api_keys_api.py."""

from __future__ import annotations

from core.passwords import hash_password
from models.account import Account

PASSWORD = "correct horse battery staple"


def _make_account(session, login: str = "apikeyuser") -> Account:
    account = Account(login=login, password_hash=hash_password(PASSWORD))
    session.add(account)
    session.commit()
    return account


def test_api_key_authenticates_in_place_of_jwt(client, db_session):
    _make_account(db_session, "apikeyuser")

    login = client.post("/v1/accounts/auth/login", json={"login": "apikeyuser", "password": PASSWORD})
    assert login.status_code == 200, login.text
    jwt_token = login.json()["access_token"]

    created = client.post(
        "/v1/accounts/me/api-keys",
        json={"scopes": ["telemetry:read"]},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert created.status_code == 201, created.text
    api_key_plaintext = created.json()["token"]

    # The whole point: this call carries NO JWT at all, only the api_key,
    # in the exact same Authorization: Bearer slot.
    listed = client.get(
        "/v1/accounts/me/api-keys",
        headers={"Authorization": f"Bearer {api_key_plaintext}"},
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1

    groups = client.get("/v1/groups", headers={"Authorization": f"Bearer {api_key_plaintext}"})
    assert groups.status_code == 200, groups.text

    created_group = client.post(
        "/v1/groups", json={"name": "via-api-key"}, headers={"Authorization": f"Bearer {api_key_plaintext}"}
    )
    assert created_group.status_code == 201, created_group.text

    revoked = client.delete(
        f"/v1/accounts/me/api-keys/{created.json()['id']}",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert revoked.status_code == 200, revoked.text

    rejected = client.get(
        "/v1/accounts/me/api-keys",
        headers={"Authorization": f"Bearer {api_key_plaintext}"},
    )
    assert rejected.status_code == 401, rejected.text


def test_garbage_bearer_token_is_rejected(client):
    response = client.get("/v1/groups", headers={"Authorization": "Bearer not-a-jwt-and-not-a-real-api-key"})
    assert response.status_code == 401
