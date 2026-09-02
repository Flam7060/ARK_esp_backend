"""ORM-модель `admin` — учётка внутренней админки. Заводится только через
CLI (нет self-signup эндпоинта) — изоляция привилегий держится на
role_code/status_code, оба FK на конечные справочники (models
.auth_lookups)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Admin(Base):
    __tablename__ = "admin"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, comment="argon2id + перец")
    role_code: Mapped[str] = mapped_column(
        Text, ForeignKey("admin_role.code"), nullable=False, default="admin", server_default="admin"
    )
    status_code: Mapped[str] = mapped_column(
        Text, ForeignKey("admin_status.code"), nullable=False, default="active", server_default="active"
    )
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin.id"), nullable=True, comment="NULL у первого (CLI)"
    )

    created_by: Mapped["Admin | None"] = relationship(remote_side=[id])
