"""ORM-модель `account` — сервисный аккаунт, автор всех ARK-данных
(last_updated_by_account_id/reported_by_account_id по всей схеме
указывают сюда). Продление подписки — не в модели: `expires_at =
max(expires_at, now()) + duration` считает сервисный слой при погашении
`activation_key`, здесь только хранение.

`failed_attempts`/`locked_until` — та же защита от перебора пароля, что
у `models.admin.Admin` (см. core/account_auth.py): у обычного account'а
брутфорс не менее реален, чем у админки, отдельной модели ради этого
заводить незачем."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (
        # Partial-unique: логин переиспользуем после soft-delete
        # (deleted_at IS NOT NULL), поэтому обычный UNIQUE на колонке
        # здесь не подходит — блокировал бы новый account с тем же login.
        Index("uq_account_login_active", "login", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_account_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    login: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, comment="argon2id + перец")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="подписка; двигается ключом"
    )
    status_code: Mapped[str] = mapped_column(
        Text, ForeignKey("account_status.code"), nullable=False, default="active", server_default="active"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Единственная группа, в которую сейчас реально льётся шеринг этого
    # account'а — NOT "группа по умолчанию для UI", а ровно то, что relay
    # (backend_go) резолвит по одному account_id, без group_id от клиента
    # (см. core/group_cache.py's set_active_group). NULL = живой шеринг
    # для этого account'а сейчас невозможен, даже если он состоит в
    # группах — установка происходит явно (create_group/join_group) или
    # руками (services.sharing_service.set_active_group).
    active_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sharing_group.id", ondelete="SET NULL"), nullable=True
    )
