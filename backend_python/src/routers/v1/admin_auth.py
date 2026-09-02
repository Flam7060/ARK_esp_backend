"""Логин админки — единственная точка выдачи admin-токена. Без него
`Depends(get_current_admin)` (core/admin_auth.py) ни на одном роутере
никогда не пройдёт: сам admin создаётся только CLI-скриптом
(scripts/create_admin.py), но токен на вход этот CLI не выдаёт."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.admin_auth import (
    AccountLockedError,
    InvalidCredentialsError,
    authenticate_admin,
    create_admin_token,
)
from core.config import config
from core.db import get_session
from routers.v1.schemas.admin_auth import AdminLoginRequest, AdminLoginResponse

router = APIRouter(prefix="/v1/admin/auth", tags=["Admin Auth"])


@router.post("/login", response_model=AdminLoginResponse, summary="Логин админки")
def login(body: AdminLoginRequest, session: Annotated[Session, Depends(get_session)]) -> AdminLoginResponse:
    """Логин по `username` + `password`. После нескольких неудачных
    попыток подряд учётка временно блокируется (423, с указанием времени
    разблокировки). Успех — JWT-токен, срок жизни — `expires_in` секунд;
    передавайте его дальше как `Authorization: Bearer <access_token>` на
    все admin-only эндпоинты."""
    try:
        admin = authenticate_admin(session, body.username, body.password)
    except AccountLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "error": {
                    "code": "account_locked",
                    "message": f"учётка заблокирована до {exc.locked_until.isoformat()}",
                    "details": {"locked_until": exc.locked_until.isoformat()},
                }
            },
        ) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "invalid_credentials", "message": "неверный логин или пароль", "details": {}}},
        ) from exc

    token = create_admin_token(admin)
    return AdminLoginResponse(access_token=token, expires_in=config.app.JWT_LIFETIME)
