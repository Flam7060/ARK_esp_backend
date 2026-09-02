"""Регистрация `account` — публичный эндпоинт (никакого admin/JWT-гейта:
это вход в систему для нового пользователя, а не операция над уже
аутентифицированным). Единственный способ завести account — по валидному
ключу активации; self-service без ключа не предусмотрен намеренно (см.
models/account.py, models/activation_key.py — "автор всех ARK-данных").

Продление подписки СУЩЕСТВУЮЩЕГО account вторым ключом — отдельная,
пока не реализованная ручка (`expires_at = max(expires_at, now()) +
duration`, а не `now() + duration`, как здесь при первичной регистрации);
не путать одно с другим.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.account_auth import AccountClaims, get_current_account
from core.db import get_session
from routers.v1.schemas.account import AccountOut, AccountRegisterRequest, ChangePasswordRequest
from routers.v1.schemas.password_reset import PasswordResetConfirmRequest
from services.account_service import (
    ConflictError,
    InvalidActivationKeyError,
    InvalidOldPasswordError,
    change_password,
    register_account,
)
from services.password_reset_service import InvalidResetTokenError, confirm_reset

router = APIRouter(prefix="/v1/accounts", tags=["Accounts"])


@router.post(
    "/register",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация по ключу активации",
)
def post_register(
    body: AccountRegisterRequest, session: Annotated[Session, Depends(get_session)]
) -> AccountOut:
    """Регистрирует новый аккаунт по `login`, `password` и валидному
    `activation_key`. Ключ можно использовать только один раз — после
    успешной регистрации он становится непригодным для повторного
    использования. Срок действия аккаунта отсчитывается от момента
    регистрации на длительность, зашитую в ключ."""
    try:
        account = register_account(session, body)
    except InvalidActivationKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "invalid_activation_key", "message": str(exc), "details": {}}},
        ) from exc
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "login_taken", "message": str(exc), "details": {}}},
        ) from exc
    return AccountOut.model_validate(account)


@router.post("/me/change-password", response_model=AccountOut, summary="Сменить пароль (FR-053)")
def post_change_password(
    body: ChangePasswordRequest,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
) -> AccountOut:
    """Меняет пароль текущего аккаунта. Требует не только валидный JWT,
    но и текущий пароль — токен доказывает лишь то, что вход когда-то
    выполнен, а старый пароль подтверждает, что запрос делает хозяин
    аккаунта, а не тот, кто перехватил ещё не истёкший токен."""
    try:
        account = change_password(session, claims.account_id, body)
    except InvalidOldPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "invalid_old_password", "message": "текущий пароль неверен", "details": {}}},
        ) from exc
    return AccountOut.model_validate(account)


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Подтвердить сброс пароля токеном от админа",
)
def post_password_reset_confirm(
    body: PasswordResetConfirmRequest, session: Annotated[Session, Depends(get_session)]
) -> None:
    """Публичный эндпоинт, токена аутентификации не требует — сам токен
    сброса и есть доказательство права сменить пароль (его выпускает
    администратор через `POST /v1/admin/accounts/{account_id}
    /password-reset-tokens` и передаёт пользователю отдельным каналом).
    Токен одноразовый, действует 1 час с момента выпуска."""
    try:
        confirm_reset(session, body.token, body.new_password)
    except InvalidResetTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "invalid_reset_token", "message": str(exc), "details": {}}},
        ) from exc
