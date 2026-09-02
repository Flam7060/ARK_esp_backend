"""Проверка JWT телеметрии — см. docs/telemetry-api-v1.md §6 ("Authorization:
Bearer <jwt> на каждый запрос, без исключений для телеметрии") и §1 A1.

Выдача токена (логин, refresh) — предмет отдельного AUTH-документа и здесь
не реализована; этот модуль только проверяет подпись и обязательные claims
уже выданного токена, тем же публичным ключом, что и `ark_relay`
(internal/authjwt в Go-сервисе) — оба сервиса должны указывать на один и
тот же файл ключа, иначе токен, валидный для одного, будет отвергнут
другим.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from core.config import config

_bearer = HTTPBearer(auto_error=False)


class ClientClaims(BaseModel):
    """Подмножество JWT-claims, на которые опирается телеметрия."""

    client_id: str
    tribe_id: str


@lru_cache(maxsize=1)
def _public_key() -> str:
    path = Path(config.jwt.PUBLIC_KEY_PATH)
    if not path.is_absolute():
        # Настройки в .env хранят путь относительно Backend/ — том же
        # каталоге, что и ENV_FILE в core/config.py.
        from core.config import ENV_FILE

        path = (ENV_FILE.parent / path).resolve()
    try:
        return path.read_text()
    except OSError as exc:
        raise RuntimeError(f"security: не удалось прочитать публичный ключ {path}: {exc}") from exc


def verify_token(raw: str) -> ClientClaims:
    """Проверяет подпись и обязательные claims. Ошибка — отказ, без частичного доверия."""
    try:
        payload = jwt.decode(
            raw,
            _public_key(),
            algorithms=["RS256"],
            leeway=config.jwt.LEEWAY_SECONDS,
            options={"require": ["exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "invalid_token", "message": str(exc), "details": {}}},
        ) from exc

    client_id = payload.get("client_id")
    tribe_id = payload.get("tribe_id")
    if not client_id or not tribe_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "invalid_token",
                    "message": "токен не содержит client_id/tribe_id",
                    "details": {},
                }
            },
        )
    return ClientClaims(client_id=client_id, tribe_id=tribe_id)


async def get_current_client(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ClientClaims:
    """FastAPI-зависимость для эндпоинтов телеметрии — см. §6."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "missing_token", "message": "Authorization: Bearer <jwt> обязателен", "details": {}}},
        )
    return verify_token(credentials.credentials)
