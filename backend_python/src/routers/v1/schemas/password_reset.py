from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from routers.v1.schemas._common import NewPassword


class PasswordResetIssueOut(BaseModel):
    token: str = Field(description="Плейнтекст токена сброса — виден только в этом ответе, передать пользователю вручную.")
    account_id: UUID
    expires_at: datetime = Field(description="Короткий TTL — токен не бессрочная замена паролю.")


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(description="Токен, полученный от админа/саппорта.")
    new_password: NewPassword
