from __future__ import annotations

from pydantic import BaseModel


class AccountLoginRequest(BaseModel):
    login: str
    password: str


class AccountLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
