"""Админ-операции над `account` — сейчас только выпуск токена сброса
пароля. Отдельный роутер/тег от `accounts.py` (self-service) и от
`admin_auth.py` (логин админки) — три разные категории даже там, где
их можно было слепить в одну ради экономии файла.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.admin_auth import AdminClaims, get_current_admin
from core.db import get_session
from routers.v1.schemas.password_reset import PasswordResetIssueOut
from services.password_reset_service import AccountNotFoundError, issue_reset_token

router = APIRouter(
    prefix="/v1/admin/accounts",
    tags=["Admin Accounts"],
    dependencies=[Depends(get_current_admin)],
)


@router.post(
    "/{account_id}/password-reset-tokens",
    response_model=PasswordResetIssueOut,
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить токен сброса пароля",
)
def post_password_reset_token(
    account_id: UUID,
    claims: Annotated[AdminClaims, Depends(get_current_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> PasswordResetIssueOut:
    """Админ НЕ задаёт и не видит новый пароль — только выпускает
    одноразовый токен с TTL (`services.password_reset_service
    .RESET_TOKEN_TTL`) и передаёт его пользователю сам, вне этого API
    (саппорт-канал). Пользователь подтверждает токен и ставит пароль сам
    через `POST /v1/accounts/password-reset/confirm`. Выпуск логируется
    (кто, кому, когда) — см. services/password_reset_service.py."""
    try:
        row, token = issue_reset_token(session, account_id, claims.admin_id)
    except AccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "account_not_found", "message": str(exc), "details": {}}},
        ) from exc
    return PasswordResetIssueOut(token=token, account_id=row.account_id, expires_at=row.expires_at)
