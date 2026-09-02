"""Общие поля pydantic-схем auth-домена — переиспользуются в регистрации,
смене пароля и подтверждении сброса (три места, одно и то же правило)."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field

MIN_PASSWORD_LENGTH = 8


def _check_password_length(v: str) -> str:
    if len(v) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password_too_short: минимум {MIN_PASSWORD_LENGTH} символов")
    return v


NewPassword = Annotated[
    str,
    Field(description=f"Не короче {MIN_PASSWORD_LENGTH} символов."),
    AfterValidator(_check_password_length),
]
