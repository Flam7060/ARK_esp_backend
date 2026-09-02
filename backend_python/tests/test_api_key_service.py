"""Тесты services/api_key_service.py — TDD. Ядро: изоляция по account_id
(чужой ключ не виден ни в get, ни в list) + отзыв не удаляет строку."""

from __future__ import annotations

import uuid

import pytest

from core.passwords import hash_password
from models.account import Account
from routers.v1.schemas.api_key import ApiKeyCreate
from services.api_key_service import NotFoundError, create_api_key, get_api_key, list_api_keys, revoke_api_key


def _make_account(session, login: str) -> Account:
    account = Account(login=login, password_hash=hash_password("correct horse battery"))
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def test_create_api_key_returns_row_and_plaintext_token_once(db_session):
    account = _make_account(db_session, "keyowner1")

    api_key, token = create_api_key(db_session, account.id, ApiKeyCreate(scopes=["telemetry:write"]))

    assert api_key.account_id == account.id
    assert api_key.status_code == "active"
    assert api_key.prefix == token[:8]
    assert api_key.last4 == token[-4:]
    assert token not in api_key.key_hash
    assert [s.scope for s in api_key.scopes] == ["telemetry:write"]


def test_get_api_key_missing_raises_not_found(db_session):
    account = _make_account(db_session, "keyowner2")

    with pytest.raises(NotFoundError):
        get_api_key(db_session, uuid.uuid4(), account.id)


def test_get_api_key_owned_by_другой_account_raises_not_found(db_session):
    owner = _make_account(db_session, "keyowner3")
    stranger = _make_account(db_session, "keystranger3")
    api_key, _ = create_api_key(db_session, owner.id, ApiKeyCreate())

    with pytest.raises(NotFoundError):
        get_api_key(db_session, api_key.id, stranger.id)


def test_list_api_keys_only_shows_own_keys(db_session):
    owner = _make_account(db_session, "keyowner4")
    stranger = _make_account(db_session, "keystranger4")
    create_api_key(db_session, owner.id, ApiKeyCreate())
    create_api_key(db_session, stranger.id, ApiKeyCreate())

    rows, _ = list_api_keys(db_session, owner.id, cursor=None, limit=10)

    assert len(rows) == 1
    assert rows[0].account_id == owner.id


def test_revoke_api_key_sets_status_without_deleting_row(db_session):
    account = _make_account(db_session, "keyowner5")
    api_key, _ = create_api_key(db_session, account.id, ApiKeyCreate())

    revoked = revoke_api_key(db_session, api_key.id, account.id)

    assert revoked.status_code == "revoked"
    # Строка всё ещё читается напрямую — не удалена.
    assert get_api_key(db_session, api_key.id, account.id).status_code == "revoked"


def test_revoke_api_key_owned_by_другой_account_raises_not_found(db_session):
    owner = _make_account(db_session, "keyowner6")
    stranger = _make_account(db_session, "keystranger6")
    api_key, _ = create_api_key(db_session, owner.id, ApiKeyCreate())

    with pytest.raises(NotFoundError):
        revoke_api_key(db_session, api_key.id, stranger.id)
