from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from routers.v1.schemas._common import NewPassword


class AccountRegisterRequest(BaseModel):
    login: str = Field(min_length=3, max_length=255, description="Логин для входа через браузер.")
    password: NewPassword
    activation_key: str = Field(description="Плейнтекст ключа активации, выданный при покупке.")


class AccountOut(BaseModel):
    id: UUID
    login: str
    expires_at: datetime | None = Field(description="Действует до этого момента — двигается погашением ключей.")
    status_code: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    """Дублирование нового пароля (ввёл дважды) — забота фронтенда, не
    этого контракта: бэкенду достаточно старого и одного нового значения."""

    old_password: str = Field(description="Текущий пароль — подтверждает, что это владелец аккаунта.")
    new_password: NewPassword
