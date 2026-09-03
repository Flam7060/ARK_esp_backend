"""Сервис групп шаринга — создание/приглашение/вступление/выход, шаблон
изоляции — как в activation_key/api_key: "не член группы" и "группы не
существует" наружу неотличимы (404), не подсказка о чужих группах."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from core.group_cache import GroupCache
from core.tokens import generate_token, hash_token
from models.account import Account
from models.sharing import GroupInviteToken, GroupMember, SharingGroup
from repositories import account_repo, sharing_repo
from routers.v1.schemas.sharing import GroupInviteCreate

__all__ = [
    "AlreadyMemberError",
    "ForbiddenError",
    "InvalidInviteError",
    "NotFoundError",
    "OwnerCannotLeaveError",
    "create_group",
    "create_invite",
    "delete_group",
    "get_group_for_member",
    "join_group",
    "leave_group",
    "list_members",
    "list_my_groups",
    "remove_member",
    "set_active_group",
]


class NotFoundError(Exception):
    """Группа не существует ИЛИ запрашивающий не её участник — одна и та
    же ошибка (см. get_group_for_member): не член группы не должен
    получать даже подтверждение, что она вообще существует."""


class ForbiddenError(Exception):
    """Действие требует роль owner, а у запрашивающего её нет."""


class InvalidInviteError(Exception):
    """Токен приглашения не найден, просрочен, отозван или исчерпан по
    max_uses — одна ошибка на все случаи."""


class AlreadyMemberError(Exception):
    """Аккаунт уже состоит в этой группе — вступать повторно незачем."""


class OwnerCannotLeaveError(Exception):
    """Owner не может выйти через /leave — либо удалить группу целиком,
    либо (в будущем) передать владение; молчаливого "стал обычным
    member" или осиротевшей группы без owner быть не должно."""


def create_group(session: Session, account_id: UUID, name: str, cache: GroupCache | None = None) -> SharingGroup:
    group = SharingGroup(name=name, owner_account_id=account_id)
    sharing_repo.insert_group(session, group)
    sharing_repo.insert_member(session, GroupMember(group_id=group.id, account_id=account_id, role_code="owner"))
    if cache is not None:
        cache.add_member(group.id, account_id)
    # Создатель начинает шарить в свою же новую группу сразу — тот же
    # принцип, что и "владелец автоматически становится owner", не
    # отдельный шаг. relay резолвит group_id именно отсюда (account_id
    # -> active_group_id), клиент больше group_id не передаёт вообще.
    _set_active_group(session, account_id, group.id, cache)
    return group


def _set_active_group(session: Session, account_id: UUID, group_id: UUID, cache: GroupCache | None) -> None:
    account = session.get(Account, account_id)
    assert account is not None  # вызывающий уже прошёл get_current_account на этом самом account_id
    account.active_group_id = group_id
    session.commit()
    if cache is not None:
        cache.set_active_group(account_id, group_id)


def _clear_active_group_if_matches(session: Session, account_id: UUID, group_id: UUID, cache: GroupCache | None) -> None:
    account = session.get(Account, account_id)
    assert account is not None
    if account.active_group_id != group_id:
        return  # ушли/выгнаны из группы, которая для этого account'а и так не была активной
    account.active_group_id = None
    session.commit()
    if cache is not None:
        cache.clear_active_group(account_id)


def list_my_groups(session: Session, account_id: UUID) -> list[SharingGroup]:
    return sharing_repo.list_groups_for_account(session, account_id)


def get_group_for_member(session: Session, group_id: UUID, account_id: UUID) -> SharingGroup:
    group = sharing_repo.get_group_by_id(session, group_id)
    if group is None or sharing_repo.get_member(session, group_id, account_id) is None:
        raise NotFoundError(f"group {group_id} not found")
    return group


def _require_owner(session: Session, group_id: UUID, account_id: UUID) -> SharingGroup:
    group = get_group_for_member(session, group_id, account_id)
    if group.owner_account_id != account_id:
        raise ForbiddenError(f"account {account_id} is not owner of group {group_id}")
    return group


def list_members(session: Session, group_id: UUID, account_id: UUID) -> list[GroupMember]:
    get_group_for_member(session, group_id, account_id)  # членство — уже допуск на просмотр списка
    return sharing_repo.list_members(session, group_id)


def create_invite(
    session: Session, group_id: UUID, account_id: UUID, data: GroupInviteCreate
) -> tuple[GroupInviteToken, str]:
    _require_owner(session, group_id, account_id)
    token = generate_token()
    invite = GroupInviteToken(
        group_id=group_id,
        token_hash=hash_token(token),
        created_by_account_id=account_id,
        grants_role_code=data.grants_role_code,
        max_uses=data.max_uses,
        expires_at=data.expires_at,
    )
    sharing_repo.insert_invite(session, invite)
    return invite, token


def join_group(session: Session, account_id: UUID, token: str, cache: GroupCache | None = None) -> GroupMember:
    invite = sharing_repo.get_invite_by_token_hash_for_update(session, hash_token(token))
    now = datetime.now(UTC)
    if (
        invite is None
        or invite.status_code != "active"
        or (invite.expires_at is not None and invite.expires_at <= now)
        or (invite.max_uses is not None and invite.uses_count >= invite.max_uses)
    ):
        raise InvalidInviteError("приглашение не найдено, просрочено, отозвано или исчерпано")

    if sharing_repo.get_member(session, invite.group_id, account_id) is not None:
        raise AlreadyMemberError(f"account {account_id} уже состоит в группе {invite.group_id}")

    member = GroupMember(group_id=invite.group_id, account_id=account_id, role_code=invite.grants_role_code)
    sharing_repo.insert_member(session, member)

    invite.uses_count += 1
    if invite.max_uses is not None and invite.uses_count >= invite.max_uses:
        invite.status_code = "expired"  # исчерпан — дальше вести себя как просроченный, не удалять историю
    session.commit()
    if cache is not None:
        cache.add_member(invite.group_id, account_id)
    # Вступление тоже переключает активную группу на эту — тот же принцип,
    # что у create_group: цель вступления обычно "теперь шарю сюда", а не
    # молчаливое членство без эффекта на шеринг.
    _set_active_group(session, account_id, invite.group_id, cache)
    return member


def leave_group(session: Session, group_id: UUID, account_id: UUID, cache: GroupCache | None = None) -> None:
    group = get_group_for_member(session, group_id, account_id)
    if group.owner_account_id == account_id:
        raise OwnerCannotLeaveError(f"owner не может покинуть группу {group_id} через /leave")
    member = sharing_repo.get_member(session, group_id, account_id)
    assert member is not None  # get_group_for_member уже проверил членство
    sharing_repo.delete_member(session, member)
    if cache is not None:
        cache.remove_member(group_id, account_id)
    _clear_active_group_if_matches(session, account_id, group_id, cache)


def remove_member(
    session: Session,
    group_id: UUID,
    requester_account_id: UUID,
    target_account_id: UUID,
    cache: GroupCache | None = None,
) -> None:
    _require_owner(session, group_id, requester_account_id)
    if target_account_id == requester_account_id:
        raise ForbiddenError("owner не может удалить себя через этот эндпоинт — используйте delete_group")
    member = sharing_repo.get_member(session, group_id, target_account_id)
    if member is None:
        raise NotFoundError(f"account {target_account_id} не состоит в группе {group_id}")
    sharing_repo.delete_member(session, member)
    if cache is not None:
        cache.remove_member(group_id, target_account_id)
    _clear_active_group_if_matches(session, target_account_id, group_id, cache)


def delete_group(session: Session, group_id: UUID, account_id: UUID, cache: GroupCache | None = None) -> None:
    group = _require_owner(session, group_id, account_id)
    member_ids = [m.account_id for m in sharing_repo.list_members(session, group_id)]
    # До delete_group, не после: ON DELETE SET NULL на самой колонке уже
    # обнулит account.active_group_id в Postgres, когда group_id ниже
    # реально снесётся, но НЕ обнулит зеркало в Redis — Postgres ничего не
    # знает про кэш Go-стороны. Список берём заранее (после сноса группы
    # active_group_id уже NULL, WHERE active_group_id = group_id ничего не
    # найдёт).
    active_account_ids = account_repo.list_ids_with_active_group(session, group_id)
    sharing_repo.delete_group(session, group)
    if cache is not None:
        cache.delete_group(group_id, member_ids)
        for active_account_id in active_account_ids:
            cache.clear_active_group(active_account_id)


def set_active_group(session: Session, account_id: UUID, group_id: UUID, cache: GroupCache | None = None) -> None:
    """Переключает, в какую из групп, где account состоит, сейчас льётся
    его шеринг — на случай, если он в нескольких сразу (create_group/
    join_group уже включают целевую группу активной автоматически, это
    ручное переключение между уже существующим членством)."""
    get_group_for_member(session, group_id, account_id)  # 404, если не участник — та же изоляция, что везде
    _set_active_group(session, account_id, group_id, cache)
