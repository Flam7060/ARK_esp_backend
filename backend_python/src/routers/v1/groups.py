"""CRUD групп шаринга — self-service, весь роутер требует
`Depends(get_current_account)`. Владение проверяется на уровне сервиса
(services/sharing_service.py), не здесь — роутер только мапит исключения
на HTTP-коды, как и остальные роутеры этого бэкенда."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.account_auth import AccountClaims, get_current_account
from core.db import get_session
from core.group_cache import GroupCache
from core.redis_sync import get_group_cache
from routers.v1.schemas.sharing import (
    GroupCreate,
    GroupInviteCreate,
    GroupInviteOut,
    GroupJoinRequest,
    GroupMemberOut,
    GroupOut,
)
from services.sharing_service import (
    AlreadyMemberError,
    ForbiddenError,
    InvalidInviteError,
    NotFoundError,
    OwnerCannotLeaveError,
    create_group,
    create_invite,
    delete_group,
    get_group_for_member,
    join_group,
    leave_group,
    list_members,
    list_my_groups,
    remove_member,
)

router = APIRouter(prefix="/v1/groups", tags=["Groups"], dependencies=[Depends(get_current_account)])


def _not_found(group_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "group_not_found", "message": f"group {group_id} не найдена", "details": {}}},
    )


def _forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": {"code": "not_group_owner", "message": message, "details": {}}},
    )


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED, summary="Создать группу")
def post_group(
    body: GroupCreate,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
    cache: Annotated[GroupCache, Depends(get_group_cache)],
) -> GroupOut:
    """Создаёт новую группу шаринга. Вызывающий аккаунт становится её
    владельцем автоматически — отдельного шага для этого не требуется."""
    group = create_group(session, claims.account_id, body.name, cache)
    return GroupOut.model_validate(group)


@router.get("", response_model=list[GroupOut], summary="Мои группы")
def get_groups(
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
) -> list[GroupOut]:
    """Возвращает все группы, в которых текущий аккаунт состоит —
    владельцем или обычным участником."""
    return [GroupOut.model_validate(g) for g in list_my_groups(session, claims.account_id)]


@router.get("/{group_id}", response_model=GroupOut, summary="Группа по id (только для участников)")
def get_group(
    group_id: UUID,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
) -> GroupOut:
    """Доступна только участникам группы. Если группа не существует или
    вызывающий аккаунт не её участник — 404 в обоих случаях, без
    разделения: посторонний не должен узнать даже сам факт существования
    чужой группы."""
    try:
        group = get_group_for_member(session, group_id, claims.account_id)
    except NotFoundError as exc:
        raise _not_found(group_id) from exc
    return GroupOut.model_validate(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Удалить группу (только owner)")
def delete_group_by_id(
    group_id: UUID,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
    cache: Annotated[GroupCache, Depends(get_group_cache)],
) -> None:
    """Удаляет группу целиком вместе со всеми участниками и активными
    приглашениями. Доступно только владельцу группы."""
    try:
        delete_group(session, group_id, claims.account_id, cache)
    except NotFoundError as exc:
        raise _not_found(group_id) from exc
    except ForbiddenError as exc:
        raise _forbidden(str(exc)) from exc


@router.get("/{group_id}/members", response_model=list[GroupMemberOut], summary="Участники группы")
def get_group_members(
    group_id: UUID,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
) -> list[GroupMemberOut]:
    """Список участников группы с их ролями. Доступна только участникам
    самой группы."""
    try:
        members = list_members(session, group_id, claims.account_id)
    except NotFoundError as exc:
        raise _not_found(group_id) from exc
    return [GroupMemberOut.model_validate(m) for m in members]


@router.delete(
    "/{group_id}/members/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить участника (только owner)",
)
def delete_group_member(
    group_id: UUID,
    account_id: UUID,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
    cache: Annotated[GroupCache, Depends(get_group_cache)],
) -> None:
    """Исключает участника из группы. Доступно только владельцу; убрать
    самого себя этим эндпоинтом нельзя — для этого есть `POST
    /{group_id}/leave` (обычному участнику) или `DELETE /{group_id}`
    (владельцу, удаляет группу целиком)."""
    try:
        remove_member(session, group_id, claims.account_id, account_id, cache)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "member_not_found", "message": str(exc), "details": {}}},
        ) from exc
    except ForbiddenError as exc:
        raise _forbidden(str(exc)) from exc


@router.post("/{group_id}/leave", status_code=status.HTTP_204_NO_CONTENT, summary="Покинуть группу")
def post_leave_group(
    group_id: UUID,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
    cache: Annotated[GroupCache, Depends(get_group_cache)],
) -> None:
    """Убирает вызывающий аккаунт из группы. Владелец группы выйти этим
    способом не может — чтобы отказаться от группы, владелец должен
    удалить её целиком через `DELETE /{group_id}`."""
    try:
        leave_group(session, group_id, claims.account_id, cache)
    except NotFoundError as exc:
        raise _not_found(group_id) from exc
    except OwnerCannotLeaveError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "owner_cannot_leave", "message": str(exc), "details": {}}},
        ) from exc


@router.post(
    "/{group_id}/invites",
    response_model=GroupInviteOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать приглашение (только owner)",
)
def post_group_invite(
    group_id: UUID,
    body: GroupInviteCreate,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
) -> GroupInviteOut:
    """Создаёт токен приглашения в группу — одноразовый или многоразовый
    (`max_uses`), с необязательным сроком действия (`expires_at`).
    Плейнтекст токена возвращается только в этом ответе — сохраните его
    сразу, повторно получить не получится. Доступно только владельцу
    группы."""
    try:
        invite, token = create_invite(session, group_id, claims.account_id, body)
    except NotFoundError as exc:
        raise _not_found(group_id) from exc
    except ForbiddenError as exc:
        raise _forbidden(str(exc)) from exc
    out = GroupInviteOut.model_validate(invite)
    return out.model_copy(update={"token": token})


@router.post("/join", response_model=GroupOut, summary="Вступить в группу по приглашению")
def post_join_group(
    body: GroupJoinRequest,
    claims: Annotated[AccountClaims, Depends(get_current_account)],
    session: Annotated[Session, Depends(get_session)],
    cache: Annotated[GroupCache, Depends(get_group_cache)],
) -> GroupOut:
    """Вступает в группу по токену приглашения, полученному от владельца
    группы. Отклоняется, если токен неизвестен, просрочен, отозван или
    уже исчерпал лимит использований (422), либо если вызывающий
    аккаунт уже состоит в этой группе (409)."""
    try:
        member = join_group(session, claims.account_id, body.token, cache)
    except InvalidInviteError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "invalid_invite", "message": str(exc), "details": {}}},
        ) from exc
    except AlreadyMemberError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "already_member", "message": str(exc), "details": {}}},
        ) from exc
    group = get_group_for_member(session, member.group_id, claims.account_id)
    return GroupOut.model_validate(group)
