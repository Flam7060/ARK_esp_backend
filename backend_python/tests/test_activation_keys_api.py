"""HTTP-контракт `/v1/admin/activation-keys` — шаблон test_users_api.py,
плюс отдельный блок про то, что вся поверхность реально закрыта
`Depends(get_current_admin)` (иначе "доступны только админу" — просто
комментарий в коде, а не факт)."""

from __future__ import annotations

from core.passwords import hash_password
from models.admin import Admin

PASSWORD = "correct horse battery staple"


def _admin_token(client, db_session, username: str = "keys_admin") -> str:
    admin = Admin(username=username, password_hash=hash_password(PASSWORD), role_code="admin")
    db_session.add(admin)
    db_session.commit()

    resp = client.post("/v1/admin/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


## --- Без токена вообще ---


def test_endpoints_reject_missing_token(client):
    assert client.post("/v1/admin/activation-keys", json={"duration": "P30D"}).status_code == 401
    assert client.get("/v1/admin/activation-keys").status_code == 401
    assert client.get("/v1/admin/activation-keys/00000000-0000-0000-0000-000000000000").status_code == 401
    assert client.patch(
        "/v1/admin/activation-keys/00000000-0000-0000-0000-000000000000", json={"tg_user_id": 1}
    ).status_code == 401
    assert client.delete("/v1/admin/activation-keys/00000000-0000-0000-0000-000000000000").status_code == 401


def test_endpoints_reject_garbage_token(client):
    resp = client.get("/v1/admin/activation-keys", headers=_auth_header("not-a-real-jwt"))
    assert resp.status_code == 401


## --- С валидным admin-токеном ---


def test_create_activation_key_returns_201_with_plaintext_token_once(client, db_session):
    token = _admin_token(client, db_session)

    resp = client.post(
        "/v1/admin/activation-keys",
        json={"duration": "P30D", "origin_code": "purchase"},
        headers=_auth_header(token),
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status_code"] == "issued"
    assert "token" in body and len(body["token"]) > 20

    # GET по id — токена в ответе уже нет (плейнтекст не хранится).
    get_resp = client.get(f"/v1/admin/activation-keys/{body['id']}", headers=_auth_header(token))
    assert get_resp.status_code == 200
    assert get_resp.json()["token"] is None


def test_get_activation_key_missing_returns_404(client, db_session):
    token = _admin_token(client, db_session, "keys_admin_404")

    resp = client.get(
        "/v1/admin/activation-keys/00000000-0000-0000-0000-000000000000", headers=_auth_header(token)
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "activation_key_not_found"


def test_patch_activation_key_updates_tg_user_id(client, db_session):
    token = _admin_token(client, db_session, "keys_admin_patch")
    created = client.post(
        "/v1/admin/activation-keys", json={"duration": "P30D"}, headers=_auth_header(token)
    ).json()

    resp = client.patch(
        f"/v1/admin/activation-keys/{created['id']}", json={"tg_user_id": 999}, headers=_auth_header(token)
    )

    assert resp.status_code == 200
    assert resp.json()["tg_user_id"] == 999


def test_delete_issued_activation_key_returns_204(client, db_session):
    token = _admin_token(client, db_session, "keys_admin_del")
    created = client.post(
        "/v1/admin/activation-keys", json={"duration": "P30D"}, headers=_auth_header(token)
    ).json()

    delete_resp = client.delete(f"/v1/admin/activation-keys/{created['id']}", headers=_auth_header(token))
    assert delete_resp.status_code == 204

    get_resp = client.get(f"/v1/admin/activation-keys/{created['id']}", headers=_auth_header(token))
    assert get_resp.status_code == 404


def test_list_activation_keys_returns_page_with_cursor(client, db_session):
    token = _admin_token(client, db_session, "keys_admin_list")
    for _ in range(3):
        client.post("/v1/admin/activation-keys", json={"duration": "P1D"}, headers=_auth_header(token))

    resp = client.get("/v1/admin/activation-keys", params={"limit": 2}, headers=_auth_header(token))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None
