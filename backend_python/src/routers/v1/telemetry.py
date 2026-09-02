"""POST /v1/telemetry/structures — UC-1, FR-1..FR-10, §5.1/§5.2/§6.

Реализован только `kind: "structures"` (архивный канал). `ally_positions`
идёт по живому каналу `ark_relay` (УК-2 — "не HTTP-снимок"), а
`enemy_positions` — открытый вопрос §9.2 ("не является рекомендацией
делать"), эндпоинт для него намеренно не создан.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import ValidationError
from redis.asyncio import Redis

from core.redis import get_redis
from core.security import ClientClaims, get_current_client
from routers.v1.schemas.telemetry import (
    MAX_ITEMS_PER_SNAPSHOT,
    RejectedItem,
    SnapshotResult,
    StructureItem,
)
from services.idempotency import already_processed, mark_processed
from services.structure_store import upsert_cached_structure

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": {}}}


def _validation_error_code(exc: ValidationError) -> str:
    """Собственные ValueError-коды валидаторов (§6: "code" — стабильная
    машиночитаемая строка) приходят от pydantic v2 с префиксом "Value
    error, " — здесь он срезается, чтобы наружу шёл голый code."""
    if not exc.errors():
        return "invalid_item"
    msg = exc.errors()[0]["msg"]
    prefix = "Value error, "
    return msg[len(prefix):] if msg.startswith(prefix) else "invalid_item"


@router.post("/structures", response_model=SnapshotResult)
async def post_structures(
    body: Annotated[dict[str, Any], Body(...)],
    claims: Annotated[ClientClaims, Depends(get_current_client)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> SnapshotResult:
    # Конверт разбирается вручную (не единой pydantic-моделью на всё тело),
    # потому что §6 требует разного поведения на двух уровнях брака:
    # плохой envelope/подпись -> весь снимок отклоняется (FR-9), плохой
    # отдельный item -> он один падает в rejected[], снимок принимается
    # (§6, "Частичная валидация внутри снимка").
    try:
        snapshot_id = UUID(str(body["snapshot_id"]))
        client_id = UUID(str(body["client_id"]))
        tribe_id = UUID(str(body["tribe_id"]))
        kind = body["kind"]
        items_raw = body["items"]
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_error("malformed_envelope", str(exc)),
        ) from exc

    if kind != "structures":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_error("unsupported_kind", f"kind={kind!r} не поддерживается этим эндпоинтом"),
        )

    # FR-9: снимок отклоняется целиком, если tribe_id токена не совпадает
    # с tribe_id, заявленным в payload.
    if str(tribe_id) != claims.tribe_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_error("invalid_tribe_scope", "tribe_id в токене не совпадает с tribe_id объекта"),
        )
    if str(client_id) != claims.client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_error("invalid_client_scope", "client_id в токене не совпадает с client_id снимка"),
        )

    if len(items_raw) > MAX_ITEMS_PER_SNAPSHOT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_error("snapshot_too_large", f"items содержит больше {MAX_ITEMS_PER_SNAPSHOT} элементов"),
        )

    # FR-2: повторная отправка того же snapshot_id не создаёт дублей.
    if await already_processed(redis, snapshot_id):
        return SnapshotResult(accepted=len(items_raw), rejected=[])

    map_id = str(body.get("map_id", ""))
    accepted = 0
    rejected: list[RejectedItem] = []

    for index, raw_item in enumerate(items_raw):
        try:
            item = StructureItem.model_validate(raw_item)
        except ValidationError as exc:
            rejected.append(RejectedItem(index=index, code=_validation_error_code(exc)))
            continue

        try:
            await upsert_cached_structure(redis, str(tribe_id), map_id, item)
        except Exception:
            logger.exception("telemetry: redis upsert failed for item %d of snapshot %s", index, snapshot_id)
            rejected.append(RejectedItem(index=index, code="storage_error"))
            continue

        accepted += 1

    await mark_processed(redis, snapshot_id)
    return SnapshotResult(accepted=accepted, rejected=rejected)
