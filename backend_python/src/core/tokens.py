"""Токены вида "плейнтекст виден один раз, в БД — только хеш" — паттерн
повторяется дословно для `activation_key.token_hash`, `api_key.key_hash`,
`group_invite_token.token_hash`, `account_password_reset_token.token_hash`
(все помечены в моделях "SHA-256, НЕ argon2"): это не пароль, который
вводит человек и который нужно защищать от офлайн-подбора медленным
хешем — это высокоэнтропийный случайный секрет, и единственная угроза, от
которой защищает хеш в БД, это чтение таблицы (бэкап, дамп, инсайдер с
доступом на SELECT). SHA-256 достаточно и на порядки дешевле argon2 на
каждой проверке (эти токены предъявляются на каждый запрос, включая
перебор).

Хешируется не голый SHA-256(token), а HMAC-SHA256(перец, token) — тем же
`config.security.PEPPER`, что и пароли (`core/passwords.py`): голый
SHA-256 от 256-битного случайного токена и так не переберёшь, но без
перца ничто не мешает найти совпадение "какой из выданных токенов
соответствует этому token_hash" простым SELECT по всей таблице дампа —
перец делает БД-дамп без .env бесполезным даже для этого.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from core.config import config

# 32 байта — 256 бит энтропии, urlsafe base64 ~43 символа: не подбирается
# перебором в разумное время, но достаточно короткое, чтобы человек мог
# продиктовать/скопировать ключ активации без переноса строки.
_TOKEN_BYTES = 32

# Доменный разделитель — HMAC с тем же ключом (PEPPER), что и
# core.passwords._pepper, но на других по смыслу входных данных: префикс
# гарантирует, что HMAC(pepper, "opaque_token:" + X) никогда не совпадёт с
# HMAC(pepper, X) из core.passwords даже при случайном совпадении X, не
# полагаясь на "предположительно разные пространства строк". Один префикс
# на ВСЕ виды токенов этого модуля (activation_key/api_key/group_invite/
# password_reset) — они всегда случайны и 256-битны, различать их между
# собой намеренно незачем: коллизия по значению между ними невозможна
# практически, а не только "маловероятна".
_DOMAIN_PREFIX = b"opaque_token:"


def generate_token() -> str:
    """Новый случайный токен — plaintext, отдаётся вызывающему ровно один
    раз (при создании), нигде не сохраняется."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Детерминированный HMAC-SHA256(перец, token) для колонки `*_hash` и
    для поиска токена по предъявленному значению
    (`WHERE token_hash = hash_token(presented)`)."""
    key = config.security.PEPPER.get_secret_value().encode("utf-8")
    return hmac.new(key, _DOMAIN_PREFIX + token.encode("utf-8"), hashlib.sha256).hexdigest()
