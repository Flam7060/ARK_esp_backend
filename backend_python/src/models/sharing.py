"""ORM-модели шаринга: `sharing_group` (НЕ игровой tribe, чисто сервисная
сущность) + `group_member` (M:N account<->group с ролью) +
`group_invite_token` (как activation_key, но приглашение в группу —
тот же паттерн FOR UPDATE + проверка лимита/срока/отзыва в сервисном
слое, не в модели)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class SharingGroup(Base):
    __tablename__ = "sharing_group"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("account.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    members: Mapped[list["GroupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")
    invite_tokens: Mapped[list["GroupInviteToken"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class GroupMember(Base):
    __tablename__ = "group_member"
    __table_args__ = (
        # Композитный PK индексирует (group_id, account_id) слева направо;
        # обратный поиск "во всех группах какого account" по одному
        # account_id нужен отдельно — синк в Redis groups:{account_id}.
        Index("ix_group_member_account_id", "account_id"),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sharing_group.id"), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("account.id"), primary_key=True)
    role_code: Mapped[str] = mapped_column(
        Text, ForeignKey("group_role.code"), nullable=False, default="member", server_default="member"
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    group: Mapped["SharingGroup"] = relationship(back_populates="members")


class GroupInviteToken(Base):
    __tablename__ = "group_invite_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sharing_group.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, comment="SHA-256; плейнтекст 1 раз")
    created_by_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=False
    )
    grants_role_code: Mapped[str] = mapped_column(
        Text, ForeignKey("group_role.code"), nullable=False, default="member", server_default="member"
    )
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="NULL = безлимит; 1 = одноразовый")
    uses_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status_code: Mapped[str] = mapped_column(
        Text, ForeignKey("invite_status.code"), nullable=False, default="active", server_default="active"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    group: Mapped["SharingGroup"] = relationship(back_populates="invite_tokens")
