"""Логин account'а — FR-052 ("Web login по UUID + password"; здесь по
`login`, не по `id`, т.к. `login` — то, что пользователь реально помнит и
вводит). Без него `Depends(get_current_account)` не проходит нигде: ни
смена пароля, ни API-ключи, ни группы."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.account_auth import (
    AccountLockedError,
    InvalidCredentialsError,
    authenticate_account,
    create_account_token,
)
from core.config import config
from core.db import get_session
from routers.v1.schemas.account_auth import AccountLoginRequest, AccountLoginResponse

router = APIRouter(prefix="/v1/accounts/auth", tags=["Account Auth"])


@router.post("/login", response_model=AccountLoginResponse, summary="Логин account'а")
def login(body: AccountLoginRequest, session: Annotated[Session, Depends(get_session)]) -> AccountLoginResponse:
    """Логин по `login` + `password`. После нескольких неудачных попыток
    подряд учётка временно блокируется (423, с указанием времени
    разблокировки). Успех — JWT-токен; передавайте его дальше как
    `Authorization: Bearer <access_token>` на все account-only
    эндпоинты."""
    try:
        account = authenticate_account(session, body.login, body.password)
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

    token = create_account_token(account)
    return AccountLoginResponse(access_token=token, expires_in=config.app.JWT_LIFETIME)
