"""Тесты scripts/create_admin.py::create_admin — та же схема, что и
test_user_service.py: TDD напрямую через Session, без запуска процесса
скрипта (getpass/argparse — не часть проверяемой логики).

Справочники (admin_role, admin_status) засеяны conftest.py::_seed_code_lookups
один раз на сессию — та же роль/статус, что реально сеет Alembic-миграция.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from create_admin import create_admin


def test_create_admin_persists_hashed_password(db_session):
    admin = create_admin(
        db_session,
        username="root",
        password="correct horse battery staple",
        role_code="admin",
        display_name="Root",
        created_by_admin_id=None,
    )

    assert admin.id is not None
    assert admin.username == "root"
    assert admin.role_code == "admin"
    assert admin.status_code == "active"  # server_default, не задан явно
    assert admin.password_hash.startswith("$argon2id$")
    assert "correct horse battery staple" not in admin.password_hash


def test_create_admin_duplicate_username_raises_integrity_error(db_session):
    create_admin(
        db_session, username="dup", password="pw-one-two-three", role_code="admin",
        display_name=None, created_by_admin_id=None,
    )

    with pytest.raises(IntegrityError):
        create_admin(
            db_session, username="dup", password="pw-four-five-six", role_code="admin",
            display_name=None, created_by_admin_id=None,
        )


def test_create_admin_unknown_role_raises_integrity_error(db_session):
    with pytest.raises(IntegrityError):
        create_admin(
            db_session, username="alice", password="pw-one-two-three", role_code="not_a_real_role",
            display_name=None, created_by_admin_id=None,
        )


def test_create_admin_records_creator_chain(db_session):
    root = create_admin(
        db_session, username="root2", password="pw-one-two-three", role_code="admin",
        display_name=None, created_by_admin_id=None,
    )

    child = create_admin(
        db_session, username="child", password="pw-one-two-three", role_code="admin",
        display_name=None, created_by_admin_id=root.id,
    )

    assert child.created_by_admin_id == root.id


def test_create_admin_missing_creator_id_raises_integrity_error(db_session):
    with pytest.raises(IntegrityError):
        create_admin(
            db_session, username="orphan", password="pw-one-two-three", role_code="admin",
            display_name=None, created_by_admin_id=uuid.uuid4(),
        )
