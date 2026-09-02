"""Pydantic-схемы группы шаринга. `GroupInviteOut.token` — плейнтекст,
только в ответе на создание приглашения (тот же паттерн, что везде)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class GroupOut(BaseModel):
    id: UUID
    name: str
    owner_account_id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberOut(BaseModel):
    account_id: UUID
    role_code: str
    joined_at: datetime

    model_config = {"from_attributes": True}


class GroupInviteCreate(BaseModel):
    grants_role_code: str = Field(default="member", description="Роль, которую получит вступивший (owner/member).")
    max_uses: int | None = Field(default=None, description="NULL = безлимит; 1 = одноразовое приглашение.")
    expires_at: datetime | None = Field(default=None, description="NULL = без срока действия.")


class GroupInviteOut(BaseModel):
    id: UUID
    token: str | None = Field(default=None, description="Плейнтекст — только в ответе на создание.")
    group_id: UUID
    grants_role_code: str
    max_uses: int | None
    uses_count: int
    status_code: str
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupJoinRequest(BaseModel):
    token: str = Field(description="Плейнтекст токена приглашения.")
