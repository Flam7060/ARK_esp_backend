"""Pydantic-схемы `activation_key`. `ActivationKeyOut.token` — плейнтекст,
заполнен ТОЛЬКО в ответе на создание (см. services/activation_key_service
.create_activation_key): токен не сохраняется нигде, кроме своего
SHA-256 в `token_hash`, поэтому повторно увидеть его через GET/list уже
нельзя — это не баг API, а прямое следствие того, что hash необратим.

`duration` сериализуется/парсится как ISO 8601 duration (`timedelta` —
дефолт pydantic v2, не наш формат): "P30D" = 30 дней, "PT1H30M" = 1 час
30 минут, "P1DT12H" = 1.5 суток. Не число секунд и не "3d" — если это не
очевидно из ответа API, то очевидно из `Field(description=...)` ниже,
которое попадает в Swagger.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field

_DURATION_DESCRIPTION = (
    "Срок действия ключа в формате ISO 8601 duration, например `P30D` (30 дней), "
    "`PT1H` (1 час), `P1DT12H` (1.5 суток). Не число секунд и не `3d`."
)


class ActivationKeyCreate(BaseModel):
    duration: timedelta = Field(description=_DURATION_DESCRIPTION, examples=["P30D"])
    origin_code: str = Field(
        default="purchase",
        description="Код источника выдачи — FK на activation_key_origin.code (seed: purchase, invite, gift).",
    )
    tg_user_id: int | None = Field(
        default=None, description="Telegram user id покупателя — идентификатор продажи, не обязателен."
    )


class ActivationKeyUpdate(BaseModel):
    """Только административные метаданные — не то, что меняет сама
    выдача/погашение ключа (status_code/redeemed_*/token_hash идут через
    отдельный, ещё не реализованный redeem-флоу, не через PATCH админки)."""

    origin_code: str | None = Field(default=None, description="Новый код источника (activation_key_origin.code).")
    tg_user_id: int | None = Field(default=None, description="Новый Telegram user id покупателя.")


class ActivationKeyOut(BaseModel):
    id: UUID
    token: str | None = Field(
        default=None,
        description="Плейнтекст токена активации. Заполнен ТОЛЬКО в ответе на POST — "
        "дальше в БД хранится необратимый SHA-256, повторно этот токен получить нельзя.",
    )
    duration: timedelta = Field(description=_DURATION_DESCRIPTION)
    origin_code: str
    status_code: str = Field(description="FK на activation_key_status.code (seed: issued, redeemed).")
    redeemed_at: datetime | None
    redeemed_by_account_id: UUID | None = Field(description="Кто погасил ключ — NULL, пока ключ не активирован.")
    tg_user_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivationKeyPage(BaseModel):
    items: list[ActivationKeyOut]
    next_cursor: str | None = Field(description="Курсор следующей страницы; null — страница последняя.")
