"""Юнит-тесты core/passwords.py — без БД: чистая функция от (пароль,
config.security.PEPPER). TDD для нового модуля: эти тесты описывают
контракт (verify только True/False, никогда исключение наружу) раньше,
чем что-либо в проекте начинает полагаться на этот модуль.
"""

from __future__ import annotations

from core.passwords import hash_password, needs_rehash, verify_password


def test_hash_password_is_argon2id_and_not_plaintext():
    hashed = hash_password("correct horse battery staple")

    assert hashed.startswith("$argon2id$")
    assert "correct horse battery staple" not in hashed


def test_hash_password_is_salted_and_nondeterministic():
    # Одна и та же пара (пароль, перец) не должна давать одинаковый хеш —
    # иначе двое пользователей с одинаковым паролем были бы видны по
    # совпадению колонки password_hash в БД.
    first = hash_password("same-password")
    second = hash_password("same-password")

    assert first != second


def test_verify_password_accepts_correct_password():
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("correct horse battery staple")

    assert verify_password("wrong password", hashed) is False


def test_verify_password_rejects_malformed_hash_without_raising():
    # Хеш повреждён/чужого формата — контракт: False, не исключение.
    # Вызывающему коду (логин-эндпоинт) не нужно оборачивать каждый вызов
    # в try/except на "а вдруг в БД мусор".
    assert verify_password("anything", "not-a-real-argon2-hash") is False


def test_needs_rehash_is_false_for_freshly_hashed_password():
    hashed = hash_password("correct horse battery staple")

    assert needs_rehash(hashed) is False
