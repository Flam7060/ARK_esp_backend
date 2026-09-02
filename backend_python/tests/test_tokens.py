"""Юнит-тесты core/tokens.py — без БД. Закрепляет, что hash_token реально
HMAC(перец, token), а не голый SHA-256: смена SECURITY_PEPPER обязана
менять результат, иначе перец существует только в докстринге."""

from __future__ import annotations

import hashlib
import hmac

from core.config import config
from core.tokens import generate_token, hash_token


def test_generate_token_is_high_entropy_and_unique():
    a, b = generate_token(), generate_token()

    assert a != b
    assert len(a) > 20


def test_hash_token_is_deterministic():
    token = generate_token()

    assert hash_token(token) == hash_token(token)


def test_hash_token_is_not_plain_sha256():
    # Если бы перец не участвовал, это было бы равенство — сам факт
    # несовпадения доказывает, что в хеш подмешан секрет, а не только
    # значение токена.
    token = generate_token()

    plain_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()

    assert hash_token(token) != plain_sha256


def test_hash_token_matches_hmac_with_configured_pepper():
    token = generate_token()

    key = config.security.PEPPER.get_secret_value().encode("utf-8")
    expected = hmac.new(key, b"opaque_token:" + token.encode("utf-8"), hashlib.sha256).hexdigest()

    assert hash_token(token) == expected


def test_hash_token_differs_between_distinct_tokens():
    a, b = generate_token(), generate_token()

    assert hash_token(a) != hash_token(b)
