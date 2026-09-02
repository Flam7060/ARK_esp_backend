"""HTTP-контракт `/v1/groups`."""

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
    assert client.post("/v1/groups", json={"name": "x"}).status_code == 401
    assert client.get("/v1/groups").status_code == 401


def test_create_list_and_get_group(client, db_session):
    token = _account_and_token(db_session, "group_owner1")

    created = client.post("/v1/groups", json={"name": "Tribe One"}, headers=_auth(token))
    assert created.status_code == 201
    group_id = created.json()["id"]

    listed = client.get("/v1/groups", headers=_auth(token))
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    fetched = client.get(f"/v1/groups/{group_id}", headers=_auth(token))
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Tribe One"


def test_non_member_gets_404_on_group(client, db_session):
    owner_token = _account_and_token(db_session, "group_owner2")
    stranger_token = _account_and_token(db_session, "group_stranger2")
    group_id = client.post("/v1/groups", json={"name": "Private"}, headers=_auth(owner_token)).json()["id"]

    resp = client.get(f"/v1/groups/{group_id}", headers=_auth(stranger_token))

    assert resp.status_code == 404


def test_invite_and_join_flow(client, db_session):
    owner_token = _account_and_token(db_session, "group_owner3")
    joiner_token = _account_and_token(db_session, "group_joiner3")
    group_id = client.post("/v1/groups", json={"name": "Joinable"}, headers=_auth(owner_token)).json()["id"]

    invite = client.post(f"/v1/groups/{group_id}/invites", json={}, headers=_auth(owner_token))
    assert invite.status_code == 201
    invite_token = invite.json()["token"]

    join = client.post("/v1/groups/join", json={"token": invite_token}, headers=_auth(joiner_token))
    assert join.status_code == 200
    assert join.json()["id"] == group_id

    members = client.get(f"/v1/groups/{group_id}/members", headers=_auth(owner_token)).json()
    assert len(members) == 2


def test_non_owner_cannot_create_invite_returns_403(client, db_session):
    owner_token = _account_and_token(db_session, "group_owner4")
    joiner_token = _account_and_token(db_session, "group_joiner4")
    group_id = client.post("/v1/groups", json={"name": "G"}, headers=_auth(owner_token)).json()["id"]
    invite_token = client.post(f"/v1/groups/{group_id}/invites", json={}, headers=_auth(owner_token)).json()["token"]
    client.post("/v1/groups/join", json={"token": invite_token}, headers=_auth(joiner_token))

    resp = client.post(f"/v1/groups/{group_id}/invites", json={}, headers=_auth(joiner_token))

    assert resp.status_code == 403


def test_owner_cannot_leave_returns_409(client, db_session):
    owner_token = _account_and_token(db_session, "group_owner5")
    group_id = client.post("/v1/groups", json={"name": "G5"}, headers=_auth(owner_token)).json()["id"]

    resp = client.post(f"/v1/groups/{group_id}/leave", headers=_auth(owner_token))

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "owner_cannot_leave"


def test_member_can_leave(client, db_session):
    owner_token = _account_and_token(db_session, "group_owner6")
    joiner_token = _account_and_token(db_session, "group_joiner6")
    group_id = client.post("/v1/groups", json={"name": "G6"}, headers=_auth(owner_token)).json()["id"]
    invite_token = client.post(f"/v1/groups/{group_id}/invites", json={}, headers=_auth(owner_token)).json()["token"]
    client.post("/v1/groups/join", json={"token": invite_token}, headers=_auth(joiner_token))

    resp = client.post(f"/v1/groups/{group_id}/leave", headers=_auth(joiner_token))

    assert resp.status_code == 204
    assert client.get(f"/v1/groups/{group_id}", headers=_auth(joiner_token)).status_code == 404


def test_delete_group_by_owner(client, db_session):
    owner_token = _account_and_token(db_session, "group_owner7")
    group_id = client.post("/v1/groups", json={"name": "G7"}, headers=_auth(owner_token)).json()["id"]

    resp = client.delete(f"/v1/groups/{group_id}", headers=_auth(owner_token))

    assert resp.status_code == 204
    assert client.get(f"/v1/groups/{group_id}", headers=_auth(owner_token)).status_code == 404
