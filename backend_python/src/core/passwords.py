"""Хеширование паролей: argon2id + перец (см. `models.admin.Admin.password_hash`,
`models.account.Account.password_hash` — обе колонки помечены "argon2id +
перец" в докстринге модели).

Перец применяется ДО argon2, через HMAC-SHA256(key=перец, msg=пароль):
  - argon2 хеширует не сырой пароль, а фиксированной длины (32 байта)
    HMAC-дайджест — длина пароля пользователя (хоть 1 символ, хоть 10000)
    не просачивается в то, что видит argon2, и не влияет на его расходы
    по памяти/времени.
  - Перец не смешивается с солью и не идёт в БД: он существует только в
    памяти процесса (`core.config.config.security.PEPPER`). Утечка одной
    базы данных без утечки .env не даёт офлайн-словарную атаку — атакующему
    нужен один и тот же HMAC-ключ, чтобы вообще пересчитать кандидатов.

argon2-cffi сам генерирует и хранит соль и параметры (time_cost/memory_cost/
parallelism) внутри итоговой строки хеша (формат `$argon2id$v=19$m=...$...`)
— `verify_password` не принимает соль отдельно, она уже в hashed.
"""

from __future__ import annotations

import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from core.config import config

# Параметры по умолчанию argon2-cffi (time_cost=3, memory_cost=64 MiB,
# parallelism=4) — OWASP-совместимый минимум для интерактивного логина
# (не для KDF, где приемлемы куда более тяжёлые параметры); менять только
# вместе с нагрузочным тестом на целевом железе, "на глаз" тут легко
# получить login-эндпоинт, съедающий воркеры под нагрузкой.
_hasher = PasswordHasher()


def _pepper(password: str) -> bytes:
    key = config.security.PEPPER.get_secret_value().encode("utf-8")
    return hmac.new(key, password.encode("utf-8"), hashlib.sha256).digest()


def hash_password(password: str) -> str:
    """Возвращает строку для колонки `password_hash` — соль и параметры
    argon2 уже внутри неё, отдельно хранить/передавать больше нечего."""
    return _hasher.hash(_pepper(password))


def verify_password(password: str, password_hash: str) -> bool:
    """True, только если пароль (с тем же перцем) совпадает с хешем.
    Любая форма "не совпало" (неверный пароль, повреждённый/чужой формат
    хеша) — False, а не исключение наружу: вызывающему всё равно, что
    именно не сошлось, только сошлось ли."""
    try:
        return _hasher.verify(password_hash, _pepper(password))
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True, если хеш посчитан на устаревших параметрах (после смены
    `_hasher` на более тяжёлые) — вызывающий код перехеширует пароль
    сразу после успешной проверки `verify_password`, не отдельным батчем."""
    return _hasher.check_needs_rehash(password_hash)
