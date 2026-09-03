"""Тесты services/sharing_service.py — TDD. Ядро: изоляция по членству
(не член = 404, не owner = 403), owner не может выйти, приглашение
гасится по правилам max_uses/expires_at/status."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.passwords import hash_password
from models.account import Account
from routers.v1.schemas.sharing import GroupInviteCreate
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


def _make_account(session, login: str) -> Account:
    account = Account(login=login, password_hash=hash_password("correct horse battery"))
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


class FakeGroupCache:
    """Стенд-ин для core.group_cache.GroupCache — проверяет, что
    sharing_service действительно зовёт SADD/SREM/PUBLISH-эквиваленты на
    каждую мутацию членства (план §5), без живого Redis."""

    def __init__(self) -> None:
        self.members: dict[uuid.UUID, set[uuid.UUID]] = {}
        self.revoked: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.active_group: dict[uuid.UUID, uuid.UUID] = {}

    def add_member(self, group_id: uuid.UUID, account_id: uuid.UUID) -> None:
        self.members.setdefault(group_id, set()).add(account_id)

    def remove_member(self, group_id: uuid.UUID, account_id: uuid.UUID) -> None:
        self.members.setdefault(group_id, set()).discard(account_id)
        self.revoked.append((group_id, account_id))

    def delete_group(self, group_id: uuid.UUID, member_ids: list[uuid.UUID]) -> None:
        for account_id in member_ids:
            self.revoked.append((group_id, account_id))
        self.members.pop(group_id, None)

    def set_active_group(self, account_id: uuid.UUID, group_id: uuid.UUID) -> None:
        self.active_group[account_id] = group_id

    def clear_active_group(self, account_id: uuid.UUID) -> None:
        self.active_group.pop(account_id, None)


def test_create_group_makes_creator_owner(db_session):
    owner = _make_account(db_session, "owner1")

    group = create_group(db_session, owner.id, "My Tribe")

    assert group.owner_account_id == owner.id
    members = list_members(db_session, group.id, owner.id)
    assert len(members) == 1
    assert members[0].role_code == "owner"


def test_non_member_cannot_see_group(db_session):
    owner = _make_account(db_session, "owner2")
    stranger = _make_account(db_session, "stranger2")
    group = create_group(db_session, owner.id, "Private")

    with pytest.raises(NotFoundError):
        get_group_for_member(db_session, group.id, stranger.id)


def test_list_my_groups_only_shows_membership(db_session):
    owner = _make_account(db_session, "owner3")
    stranger = _make_account(db_session, "stranger3")
    create_group(db_session, owner.id, "Group A")

    assert len(list_my_groups(db_session, owner.id)) == 1
    assert len(list_my_groups(db_session, stranger.id)) == 0


def test_join_group_via_invite_adds_member(db_session):
    owner = _make_account(db_session, "owner4")
    joiner = _make_account(db_session, "joiner4")
    group = create_group(db_session, owner.id, "Group B")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())

    member = join_group(db_session, joiner.id, token)

    assert member.role_code == "member"
    members = {m.account_id for m in list_members(db_session, group.id, owner.id)}
    assert joiner.id in members


def test_non_owner_cannot_create_invite(db_session):
    owner = _make_account(db_session, "owner5")
    joiner = _make_account(db_session, "joiner5")
    group = create_group(db_session, owner.id, "Group C")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token)

    with pytest.raises(ForbiddenError):
        create_invite(db_session, group.id, joiner.id, GroupInviteCreate())


def test_join_group_rejects_unknown_token(db_session):
    joiner = _make_account(db_session, "joiner6")

    with pytest.raises(InvalidInviteError):
        join_group(db_session, joiner.id, "not-a-real-token")


def test_join_group_rejects_already_member(db_session):
    owner = _make_account(db_session, "owner7")
    joiner = _make_account(db_session, "joiner7")
    group = create_group(db_session, owner.id, "Group D")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token)

    _, token2 = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    with pytest.raises(AlreadyMemberError):
        join_group(db_session, joiner.id, token2)


def test_join_group_respects_max_uses(db_session):
    owner = _make_account(db_session, "owner8")
    first = _make_account(db_session, "first8")
    second = _make_account(db_session, "second8")
    group = create_group(db_session, owner.id, "Group E")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate(max_uses=1))

    join_group(db_session, first.id, token)

    with pytest.raises(InvalidInviteError):
        join_group(db_session, second.id, token)


def test_join_group_rejects_expired_invite(db_session):
    owner = _make_account(db_session, "owner9")
    joiner = _make_account(db_session, "joiner9")
    group = create_group(db_session, owner.id, "Group F")
    _, token = create_invite(
        db_session, group.id, owner.id, GroupInviteCreate(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )

    with pytest.raises(InvalidInviteError):
        join_group(db_session, joiner.id, token)


def test_owner_cannot_leave_group(db_session):
    owner = _make_account(db_session, "owner10")
    group = create_group(db_session, owner.id, "Group G")

    with pytest.raises(OwnerCannotLeaveError):
        leave_group(db_session, group.id, owner.id)


def test_member_can_leave_group(db_session):
    owner = _make_account(db_session, "owner11")
    joiner = _make_account(db_session, "joiner11")
    group = create_group(db_session, owner.id, "Group H")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token)

    leave_group(db_session, group.id, joiner.id)

    with pytest.raises(NotFoundError):
        get_group_for_member(db_session, group.id, joiner.id)


def test_owner_can_remove_member(db_session):
    owner = _make_account(db_session, "owner12")
    joiner = _make_account(db_session, "joiner12")
    group = create_group(db_session, owner.id, "Group I")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token)

    remove_member(db_session, group.id, owner.id, joiner.id)

    with pytest.raises(NotFoundError):
        get_group_for_member(db_session, group.id, joiner.id)


def test_non_owner_cannot_remove_member(db_session):
    owner = _make_account(db_session, "owner13")
    a = _make_account(db_session, "membera13")
    b = _make_account(db_session, "memberb13")
    group = create_group(db_session, owner.id, "Group J")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate(max_uses=None))
    join_group(db_session, a.id, token)
    _, token2 = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, b.id, token2)

    with pytest.raises(ForbiddenError):
        remove_member(db_session, group.id, a.id, b.id)


def test_owner_cannot_remove_self_via_remove_member(db_session):
    owner = _make_account(db_session, "owner14")
    group = create_group(db_session, owner.id, "Group K")

    with pytest.raises(ForbiddenError):
        remove_member(db_session, group.id, owner.id, owner.id)


def test_delete_group_removes_it_and_members(db_session):
    owner = _make_account(db_session, "owner15")
    joiner = _make_account(db_session, "joiner15")
    group = create_group(db_session, owner.id, "Group L")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token)

    delete_group(db_session, group.id, owner.id)

    with pytest.raises(NotFoundError):
        get_group_for_member(db_session, group.id, owner.id)


def test_non_owner_cannot_delete_group(db_session):
    owner = _make_account(db_session, "owner16")
    joiner = _make_account(db_session, "joiner16")
    group = create_group(db_session, owner.id, "Group M")
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token)

    with pytest.raises(ForbiddenError):
        delete_group(db_session, group.id, joiner.id)


def test_delete_group_missing_raises_not_found(db_session):
    owner = _make_account(db_session, "owner17")

    with pytest.raises(NotFoundError):
        delete_group(db_session, uuid.uuid4(), owner.id)


def test_create_group_adds_owner_to_cache(db_session):
    owner = _make_account(db_session, "owner18")
    cache = FakeGroupCache()

    group = create_group(db_session, owner.id, "Group N", cache)

    assert cache.members[group.id] == {owner.id}


def test_join_group_adds_member_to_cache(db_session):
    owner = _make_account(db_session, "owner19")
    joiner = _make_account(db_session, "joiner19")
    cache = FakeGroupCache()
    group = create_group(db_session, owner.id, "Group O", cache)
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())

    join_group(db_session, joiner.id, token, cache)

    assert cache.members[group.id] == {owner.id, joiner.id}


def test_leave_group_removes_from_cache_and_publishes_revoke(db_session):
    owner = _make_account(db_session, "owner20")
    joiner = _make_account(db_session, "joiner20")
    cache = FakeGroupCache()
    group = create_group(db_session, owner.id, "Group P", cache)
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token, cache)

    leave_group(db_session, group.id, joiner.id, cache)

    assert cache.members[group.id] == {owner.id}
    assert (group.id, joiner.id) in cache.revoked


def test_remove_member_removes_from_cache_and_publishes_revoke(db_session):
    owner = _make_account(db_session, "owner21")
    joiner = _make_account(db_session, "joiner21")
    cache = FakeGroupCache()
    group = create_group(db_session, owner.id, "Group Q", cache)
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token, cache)

    remove_member(db_session, group.id, owner.id, joiner.id, cache)

    assert cache.members[group.id] == {owner.id}
    assert (group.id, joiner.id) in cache.revoked


def test_delete_group_publishes_revoke_for_every_member_and_clears_cache(db_session):
    owner = _make_account(db_session, "owner22")
    joiner = _make_account(db_session, "joiner22")
    cache = FakeGroupCache()
    group = create_group(db_session, owner.id, "Group R", cache)
    _, token = create_invite(db_session, group.id, owner.id, GroupInviteCreate())
    join_group(db_session, joiner.id, token, cache)

    delete_group(db_session, group.id, owner.id, cache)

    assert group.id not in cache.members
    assert {owner.id, joiner.id} == {aid for gid, aid in cache.revoked if gid == group.id}
