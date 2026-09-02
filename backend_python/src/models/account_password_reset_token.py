"""ORM-модель `account_password_reset_token` — самостоятельного сброса
пароля НЕТ (продуктовое решение: у account нет email/телефона, слать код
некуда). Единственный путь восстановления — админ выпускает токен через
`POST /v1/admin/accounts/{account_id}/password-reset` и передаёт его
пользователю вручную (саппорт-канал вне этого бэкенда); сам админ пароль
не видит и не задаёт — только токен на одноразовую смену.

`created_by_admin_id` не nullable — это и есть журнал ("кто выпустил
токен, когда"), не опциональная деталь; второй слой аудита —
`logger.info(...)` в services/password_reset_service.py на выпуск и на
погашение."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class AccountPasswordResetToken(Base):
    __tablename__ = "account_password_reset_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True, comment="SHA-256, НЕ argon2")
    created_by_admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin.id"), nullable=False, comment="кто выпустил — обязательный аудит"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="короткий TTL — не бессрочная замена паролю"
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="NULL = ещё не погашен; погашается ровно 1 раз"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
