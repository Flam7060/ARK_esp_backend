"""CRUD-роутер `activation_key` — только для админки: каждый эндпоинт
защищён `Depends(get_current_admin)` (core/admin_auth.py). Обычные
account/сервис-клиенты сюда не имеют доступа никаким токеном, кроме
admin-токена, выданного `POST /v1/admin/auth/login`.

Тег "Activation Keys" отдельный от "Admin Auth" (см. admin_auth.py) —
это разные категории в Swagger: одна про то, как получить admin-токен,
другая — что этим токеном можно делать. Смешивать их в один "admin" было
неинформативно при росте числа admin-ресурсов.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.admin_auth import get_current_admin
from core.db import get_session
from core.pagination import InvalidCursorError
from routers.v1.schemas.activation_key import (
    ActivationKeyCreate,
    ActivationKeyOut,
    ActivationKeyPage,
    ActivationKeyUpdate,
)
from services.activation_key_service import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ConflictError,
    NotDeletableError,
    NotFoundError,
    create_activation_key,
    delete_activation_key,
    get_activation_key,
    list_activation_keys,
    update_activation_key,
)

router = APIRouter(
    prefix="/v1/admin/activation-keys",
    tags=["Activation Keys"],
    dependencies=[Depends(get_current_admin)],
)


def _not_found(key_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": {"code": "activation_key_not_found", "message": f"activation_key {key_id} не найден", "details": {}}
        },
    )


@router.post(
    "",
    response_model=ActivationKeyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить новый ключ активации",
)
def post_activation_key(
    body: ActivationKeyCreate, session: Annotated[Session, Depends(get_session)]
) -> ActivationKeyOut:
    """Генерирует случайный токен (256 бит энтропии), кладёт в БД только
    его SHA-256 (`token_hash`), а плейнтекст возвращает в поле `token`
    ЭТОГО ответа и больше нигде — ни `GET`, ни `PATCH` его не покажут.
    Отдай токен покупателю сразу же: второго шанса прочитать его нет."""
    try:
        activation_key, token = create_activation_key(session, body)
    except ConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "token_collision", "message": str(exc), "details": {}}},
        ) from exc
    # token — плейнтекст, единственный раз за весь жизненный цикл ключа:
    # ORM-объект хранит только token_hash, поэтому его руками докладываем
    # в ответ, а не читаем из activation_key.
    out = ActivationKeyOut.model_validate(activation_key)
    return out.model_copy(update={"token": token})


@router.get("", response_model=ActivationKeyPage, summary="Список ключей активации")
def get_activation_keys(
    session: Annotated[Session, Depends(get_session)],
    cursor: str | None = Query(default=None, description="Курсор со страницы, полученной ранее."),
    limit: int = Query(default=DEFAULT_LIMIT, gt=0, le=MAX_LIMIT, description="Размер страницы."),
) -> ActivationKeyPage:
    """Курсорная пагинация (не offset/limit — растущая таблица не даёт
    offset'у согласованных страниц между запросами), сортировка по
    `created_at` убыванием. `token` в списке всегда `null` — плейнтекст
    виден только один раз, в ответе на создание."""
    try:
        rows, next_cursor = list_activation_keys(session, cursor, limit)
    except InvalidCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "invalid_cursor", "message": str(exc), "details": {}}},
        ) from exc
    return ActivationKeyPage(items=[ActivationKeyOut.model_validate(k) for k in rows], next_cursor=next_cursor)


@router.get("/{key_id}", response_model=ActivationKeyOut, summary="Получить ключ активации по id")
def get_activation_key_by_id(
    key_id: UUID, session: Annotated[Session, Depends(get_session)]
) -> ActivationKeyOut:
    """`token` в ответе всегда `null` — см. docstring `post_activation_key`."""
    try:
        activation_key = get_activation_key(session, key_id)
    except NotFoundError as exc:
        raise _not_found(key_id) from exc
    return ActivationKeyOut.model_validate(activation_key)


@router.patch("/{key_id}", response_model=ActivationKeyOut, summary="Изменить метаданные ключа")
def patch_activation_key(
    key_id: UUID, body: ActivationKeyUpdate, session: Annotated[Session, Depends(get_session)]
) -> ActivationKeyOut:
    """Меняет только `origin_code`/`tg_user_id` — административные
    метаданные. `status_code`/`redeemed_*`/сам токен через этот эндпоинт
    не меняются: их меняет флоу погашения ключа аккаунтом, не админка."""
    try:
        activation_key = update_activation_key(session, key_id, body)
    except NotFoundError as exc:
        raise _not_found(key_id) from exc
    return ActivationKeyOut.model_validate(activation_key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Удалить неиспользованный ключ")
def delete_activation_key_by_id(key_id: UUID, session: Annotated[Session, Depends(get_session)]) -> None:
    """Удаляет ключ только в статусе `issued`. Погашенный (`redeemed`)
    ключ удалить нельзя — это стёрло бы единственную запись о том, каким
    ключом конкретный account активировал подписку (аудит навсегда)."""
    try:
        delete_activation_key(session, key_id)
    except NotFoundError as exc:
        raise _not_found(key_id) from exc
    except NotDeletableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "activation_key_not_deletable", "message": str(exc), "details": {}}},
        ) from exc
