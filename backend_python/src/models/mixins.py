"""Общий предок для code/label справочников AUTH и ARK. PK — сам код, не
suid: это конечный список значений, заведённый seed-данными (не растёт
рантаймом приложения), на него ссылаются FK из бизнес-таблиц напрямую по
коду (`admin.role_code -> admin_role.code`)."""

from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class CodeLookup(Base):
    __abstract__ = True

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
