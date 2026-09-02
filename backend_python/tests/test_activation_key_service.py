"""Тесты services/activation_key_service.py — TDD, шаблон test_user_service.py."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from routers.v1.schemas.activation_key import ActivationKeyCreate, ActivationKeyUpdate
from services.activation_key_service import (
    NotDeletableError,
    NotFoundError,
    create_activation_key,
    delete_activation_key,
    get_activation_key,
    list_activation_keys,
    update_activation_key,
)


def test_create_activation_key_returns_row_and_plaintext_token_once(db_session):
    row, token = create_activation_key(
        db_session, ActivationKeyCreate(duration=timedelta(days=30), origin_code="purchase")
    )

    assert row.id is not None
    assert row.status_code == "issued"
    assert row.duration == timedelta(days=30)
    assert len(token) > 20
    # В БД — только хеш, не плейнтекст.
    assert token not in row.token_hash


def test_get_activation_key_missing_raises_not_found(db_session):
    with pytest.raises(NotFoundError):
        get_activation_key(db_session, uuid.uuid4())


def test_update_activation_key_changes_only_provided_fields(db_session):
    row, _ = create_activation_key(
        db_session, ActivationKeyCreate(duration=timedelta(days=7), origin_code="purchase", tg_user_id=111)
    )

    updated = update_activation_key(db_session, row.id, ActivationKeyUpdate(tg_user_id=222))

    assert updated.tg_user_id == 222
    assert updated.origin_code == "purchase"  # не тронуто


def test_update_activation_key_missing_raises_not_found(db_session):
    with pytest.raises(NotFoundError):
        update_activation_key(db_session, uuid.uuid4(), ActivationKeyUpdate(tg_user_id=1))


def test_delete_issued_activation_key_removes_row(db_session):
    row, _ = create_activation_key(db_session, ActivationKeyCreate(duration=timedelta(days=7)))

    delete_activation_key(db_session, row.id)

    with pytest.raises(NotFoundError):
        get_activation_key(db_session, row.id)


def test_delete_redeemed_activation_key_raises_not_deletable(db_session):
    row, _ = create_activation_key(db_session, ActivationKeyCreate(duration=timedelta(days=7)))
    row.status_code = "redeemed"
    db_session.commit()

    with pytest.raises(NotDeletableError):
        delete_activation_key(db_session, row.id)


def test_delete_missing_raises_not_found(db_session):
    with pytest.raises(NotFoundError):
        delete_activation_key(db_session, uuid.uuid4())


def test_list_activation_keys_paginates_with_cursor(db_session):
    for _ in range(5):
        create_activation_key(db_session, ActivationKeyCreate(duration=timedelta(days=1)))

    first_page, cursor = list_activation_keys(db_session, cursor=None, limit=3)
    assert len(first_page) == 3
    assert cursor is not None

    second_page, cursor2 = list_activation_keys(db_session, cursor=cursor, limit=3)
    assert len(second_page) == 2
    assert cursor2 is None

    assert {k.id for k in first_page}.isdisjoint({k.id for k in second_page})
