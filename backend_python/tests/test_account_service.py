"""Тесты services/account_service.register_account — TDD.

Проверяет ядро бизнес-правила: ключ гасится РОВНО когда и только когда
создан account, ключ не годится дважды, login занят -> конфликт, а
ключ при этом остаётся issued (откат не должен погасить чужой ключ)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.tokens import generate_token, hash_token
from models.activation_key import ActivationKey
from repositories import activation_key_repo
from routers.v1.schemas.account import AccountRegisterRequest
from services.account_service import ConflictError, InvalidActivationKeyError, register_account


def _make_key(session, duration: timedelta = timedelta(days=30)) -> tuple[ActivationKey, str]:
    token = generate_token()
    key = ActivationKey(token_hash=hash_token(token), duration=duration)
    activation_key_repo.insert(session, key)
    return key, token


def test_register_account_redeems_key_and_sets_expiry(db_session):
    key, token = _make_key(db_session, timedelta(days=30))
    before = datetime.now(UTC)

    account = register_account(
        db_session, AccountRegisterRequest(login="player1", password="correct horse", activation_key=token)
    )

    assert account.id is not None
    assert account.login == "player1"
    assert account.expires_at is not None
    assert account.expires_at >= before + timedelta(days=29, hours=23)

    db_session.refresh(key)
    assert key.status_code == "redeemed"
    assert key.redeemed_by_account_id == account.id
    assert key.redeemed_at is not None


def test_register_account_password_is_hashed_not_plaintext(db_session):
    _, token = _make_key(db_session)

    account = register_account(
        db_session, AccountRegisterRequest(login="player2", password="correct horse", activation_key=token)
    )

    assert account.password_hash.startswith("$argon2id$")
    assert "correct horse" not in account.password_hash


def test_register_account_rejects_unknown_token(db_session):
    with pytest.raises(InvalidActivationKeyError):
        register_account(
            db_session,
            AccountRegisterRequest(login="ghost", password="correct horse", activation_key="not-a-real-token"),
        )


def test_register_account_rejects_already_redeemed_key(db_session):
    key, token = _make_key(db_session)
    register_account(
        db_session, AccountRegisterRequest(login="first_user", password="correct horse", activation_key=token)
    )

    with pytest.raises(InvalidActivationKeyError):
        register_account(
            db_session,
            AccountRegisterRequest(login="second_user", password="correct horse", activation_key=token),
        )

    db_session.refresh(key)
    assert key.status_code == "redeemed"  # не перезаписан вторым (провалившимся) вызовом


def test_register_account_duplicate_login_raises_conflict_and_key_stays_issued(db_session):
    register_account(
        db_session,
        AccountRegisterRequest(
            login="dup_login", password="correct horse", activation_key=_make_key(db_session)[1]
        ),
    )
    second_key, second_token = _make_key(db_session)

    with pytest.raises(ConflictError):
        register_account(
            db_session,
            AccountRegisterRequest(login="dup_login", password="correct horse", activation_key=second_token),
        )

    # Ключ, которым пытались зарегистрироваться повторным login, не должен
    # оказаться сожжённым — конфликт по login откатывает всю операцию.
    db_session.refresh(second_key)
    assert second_key.status_code == "issued"
