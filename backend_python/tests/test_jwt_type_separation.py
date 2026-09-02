"""Admin-токен и account-токен подписаны ОДНИМ секретом (APP_SECRET_KEY)
— единственное, что не даёт одному сойти за другой, это claim `typ`.
Проверяет обе стороны: admin-токен не проходит как account, и наоборот
(вторая сторона — tests/test_password_reset.py
::test_account_token_cannot_be_used_as_admin_token)."""

from __future__ import annotations

from core.admin_auth import create_admin_token
from core.passwords import hash_password
from models.admin import Admin


def _make_admin(session, username: str = "typ_admin") -> Admin:
    admin = Admin(username=username, password_hash=hash_password("adminpass123"), role_code="admin")
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def test_admin_token_cannot_be_used_as_account_token(client, db_session):
    admin = _make_admin(db_session)
    admin_token = create_admin_token(admin)

    resp = client.get("/v1/groups", headers={"Authorization": f"Bearer {admin_token}"})

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"]["code"] == "invalid_account_token"
