"""GET /v1/tribes/{tribe_id}/structures — UC-4, §6 (курсорная пагинация).

Роутер только парсит вход/выход и проверяет tribe-scope из JWT — SQL и
курсор-логика в services/structure_query_service.py +
repositories/structure_repo.py (см. routers/v1/users.py — тот же принцип).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.db import get_session
from core.pagination import InvalidCursorError
from core.security import ClientClaims, get_current_client
from routers.v1.schemas.telemetry import StructureOut, StructurePage
from services.structure_query_service import DEFAULT_LIMIT, MAX_LIMIT, list_structures

router = APIRouter(prefix="/v1/tribes", tags=["tribes"])


@router.get("/{tribe_id}/structures", response_model=StructurePage, summary="Постройки трайба")
def get_structures(
    tribe_id: UUID,
    claims: Annotated[ClientClaims, Depends(get_current_client)],
    session: Annotated[Session, Depends(get_session)],
    map_id: str | None = Query(default=None, description="Ограничить одной картой (опционально)."),
    cursor: str | None = Query(default=None, description="Курсор со страницы, полученной ранее."),
    limit: int = Query(default=DEFAULT_LIMIT, gt=0, le=MAX_LIMIT, description="Размер страницы."),
) -> StructurePage:
    """Список построек трайба, ранее загруженных через `POST
    /v1/telemetry/structures`. `tribe_id` в пути должен совпадать с
    `tribe_id` в токене клиента — доступа к чужому трайбу нет (403).
    Постраничный вывод — курсорный: передайте `next_cursor` из
    предыдущего ответа в `cursor`, чтобы получить следующую страницу."""
    if str(tribe_id) != claims.tribe_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "invalid_tribe_scope", "message": "нет доступа к чужому трайбу", "details": {}}},
        )

    try:
        rows, next_cursor = list_structures(session, tribe_id, map_id, cursor, limit)
    except InvalidCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "invalid_cursor", "message": str(exc), "details": {}}},
        ) from exc

    return StructurePage(items=[StructureOut.model_validate(r) for r in rows], next_cursor=next_cursor)
