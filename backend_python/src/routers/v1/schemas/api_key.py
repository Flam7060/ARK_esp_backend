"""Pydantic-схемы `api_key`. `ApiKeyOut.token` — плейнтекст, только в
ответе на создание (тот же паттерн, что activation_key/token). `prefix`+
`last4` остаются видимыми всегда — по ним пользователь узнаёт СВОЙ ключ
в списке, не читая token_hash и не имея плейнтекста под рукой."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ApiKeyCreate(BaseModel):
    scopes: list[str] = Field(default_factory=list, description="Права ключа, например ['telemetry:write'].")
    expires_at: datetime | None = Field(default=None, description="NULL — бессрочный (до ручного revoke).")


class ApiKeyOut(BaseModel):
    id: UUID
    token: str | None = Field(default=None, description="Плейнтекст — только в ответе на создание.")
    prefix: str
    last4: str
    status_code: str
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("scopes", mode="before")
    @classmethod
    def _extract_scope_strings(cls, v: object) -> list[str]:
        """`ApiKey.scopes` — relationship на `ApiKeyScope` (объекты с
        `.scope`), не `list[str]` напрямую; здесь же принимаем и уже
        готовый `list[str]` (эхо созданных scopes в ответе на POST) —
        одна схема на оба источника, а не два разных класса."""
        if v is None:
            return []
        return [item.scope if hasattr(item, "scope") else item for item in v]


class ApiKeyPage(BaseModel):
    items: list[ApiKeyOut]
    next_cursor: str | None
