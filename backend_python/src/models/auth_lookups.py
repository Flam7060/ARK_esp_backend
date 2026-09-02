"""Справочники AUTH-домена. Конечные списки — заводятся seed-миграцией,
приложение в них не пишет; FK из бизнес-таблиц ссылаются на `code`."""

from __future__ import annotations

from sqlalchemy import Boolean
from sqlalchemy.orm import Mapped, mapped_column

from models.mixins import CodeLookup


class AdminRole(CodeLookup):
    """seed: superadmin, admin, support, developer"""

    __tablename__ = "admin_role"


class AdminStatus(CodeLookup):
    """seed: active, disabled, locked"""

    __tablename__ = "admin_status"


class AccountStatus(CodeLookup):
    """seed: active, suspended"""

    __tablename__ = "account_status"


class ActivationKeyOrigin(CodeLookup):
    """seed: purchase, invite, gift"""

    __tablename__ = "activation_key_origin"


class ActivationKeyStatus(CodeLookup):
    """seed: issued(false), redeemed(true)"""

    __tablename__ = "activation_key_status"

    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class ApiKeyStatus(CodeLookup):
    """seed: active(false), revoked(true)"""

    __tablename__ = "api_key_status"

    is_terminal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class GroupRole(CodeLookup):
    """seed: owner, member"""

    __tablename__ = "group_role"


class InviteStatus(CodeLookup):
    """seed: active, revoked, expired"""

    __tablename__ = "invite_status"
