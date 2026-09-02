"""CRUD `api_key` — self-service: аутентифицированный account управляет
только своими ключами (`Depends(get_current_account)` + фильтр по
account_id на каждом запросе к БД, см. repositories/api_key_repo.py)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.account_auth import AccountClaims, get_current_account
from core.api_key_cache import RedisApiKeyCache
from core.db import get_session
from core.pagination import InvalidCursorError
from core.redis_sync import get_api_key_cache
from routers.v1.schemas.api_key import ApiKeyCreate, ApiKeyOut, ApiKeyPage
from services.api_key_service import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    NotFoundError,
    create_api_key,
    get_api_key,
    list_api_keys,
    revoke_api_key,
)

router = APIRouter(
    prefix="/v1/accounts/me/api-keys",
    tags=["API Keys"],
    dependencies=[Depends(get_current_account)],
)


def _not_found(key_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "api_key_not_found", "message": f"api_key {key_id} не найден", "details": {}}},
    )


@router.post("", response_model=ApiKeyOut, status_code=status.HTTP_201_CREATED, summary="Создать API-ключ")
def post_api_key(
    body: ApiKeyCreate,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
    cache: Annotated[RedisApiKeyCache, Depends(get_api_key_cache)],
) -> ApiKeyOut:
    """Плейнтекст ключа виден только в этом ответе — в БД остаётся лишь
    его HMAC-SHA256 (см. core/tokens.py). Второго шанса прочитать его нет."""
    api_key, token = create_api_key(session, claims.account_id, body, cache)
    out = ApiKeyOut.model_validate(api_key)
    return out.model_copy(update={"token": token})


@router.get("", response_model=ApiKeyPage, summary="Список своих API-ключей")
def get_api_keys(
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, gt=0, le=MAX_LIMIT),
) -> ApiKeyPage:
    try:
        rows, next_cursor = list_api_keys(session, claims.account_id, cursor, limit)
    except InvalidCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "invalid_cursor", "message": str(exc), "details": {}}},
        ) from exc
    return ApiKeyPage(items=[ApiKeyOut.model_validate(k) for k in rows], next_cursor=next_cursor)


@router.get("/{key_id}", response_model=ApiKeyOut, summary="Получить свой API-ключ по id")
def get_api_key_by_id(
    key_id: UUID,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
) -> ApiKeyOut:
    try:
        api_key = get_api_key(session, key_id, claims.account_id)
    except NotFoundError as exc:
        raise _not_found(key_id) from exc
    return ApiKeyOut.model_validate(api_key)


@router.delete("/{key_id}", response_model=ApiKeyOut, summary="Отозвать API-ключ")
def delete_api_key(
    key_id: UUID,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
    cache: Annotated[RedisApiKeyCache, Depends(get_api_key_cache)],
) -> ApiKeyOut:
    """Не удаляет строку — переводит `status_code` в `revoked` (терминал).
    Идемпотентно вызывать повторно на уже отозванном ключе можно, второй
    вызов просто ничего не меняет."""
    try:
        api_key = revoke_api_key(session, key_id, claims.account_id, cache)
    except NotFoundError as exc:
        raise _not_found(key_id) from exc
    return ApiKeyOut.model_validate(api_key)
