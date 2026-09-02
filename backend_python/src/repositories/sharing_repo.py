"""Репозиторий шаринга — `sharing_group`/`group_member`/`group_invite_token`
в одном файле, как и models/sharing.py: три тесно связанные таблицы одного
домена, а не искусственно разнесённые ради "одна модель — один файл"."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.sharing import GroupInviteToken, GroupMember, SharingGroup

## --- sharing_group ---


def insert_group(session: Session, group: SharingGroup) -> SharingGroup:
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def get_group_by_id(session: Session, group_id: UUID) -> SharingGroup | None:
    return session.get(SharingGroup, group_id)


def list_groups_for_account(session: Session, account_id: UUID) -> list[SharingGroup]:
    stmt = (
        select(SharingGroup)
        .join(GroupMember, GroupMember.group_id == SharingGroup.id)
        .where(GroupMember.account_id == account_id)
        .order_by(SharingGroup.created_at.desc())
    )
    return list(session.execute(stmt).scalars())


def delete_group(session: Session, group: SharingGroup) -> None:
    session.delete(group)  # cascade="all, delete-orphan" на relationship — тянет members/invite_tokens
    session.commit()


## --- group_member ---


def insert_member(session: Session, member: GroupMember) -> GroupMember:
    session.add(member)
    session.commit()
    return member


def get_member(session: Session, group_id: UUID, account_id: UUID) -> GroupMember | None:
    return session.get(GroupMember, (group_id, account_id))


def list_members(session: Session, group_id: UUID) -> list[GroupMember]:
    stmt = select(GroupMember).where(GroupMember.group_id == group_id).order_by(GroupMember.joined_at.asc())
    return list(session.execute(stmt).scalars())


def delete_member(session: Session, member: GroupMember) -> None:
    session.delete(member)
    session.commit()


## --- group_invite_token ---


def insert_invite(session: Session, invite: GroupInviteToken) -> GroupInviteToken:
    session.add(invite)
    session.commit()
    session.refresh(invite)
    return invite


def get_invite_by_token_hash_for_update(session: Session, token_hash: str) -> GroupInviteToken | None:
    stmt = select(GroupInviteToken).where(GroupInviteToken.token_hash == token_hash).with_for_update()
    return session.execute(stmt).scalar_one_or_none()
