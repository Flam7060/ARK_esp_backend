"""Служебные ручки процесса: HTML-портал документации (`/`) и readiness-
healthcheck (`/healthz`). Без `/v1`-префикса — это не версионируемое REST
API, а инфраструктурные эндпоинты (docker-compose healthcheck, браузер
разработчика), тот же принцип, что развёл routers/v1/* по файлам, здесь
развёл именно их, а не main.py, в отдельный роутер.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import config
from core.db import get_session
from core.redis import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(tags=["status"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def docs_portal() -> str:
    """Единая точка входа в документацию: REST (OpenAPI, генерируется
    FastAPI из кода на каждый запрос — всегда актуален сам) и WebSocket-
    протокол ark_relay (AsyncAPI, статика — обновляется руками вместе с
    ark_relay/internal/protocol/message.go, см. docs/asyncapi.yaml там же).
    Ссылки, а не слитый в один UI контент: OpenAPI и AsyncAPI — разные
    форматы спецификаций (запрос-ответ vs message-driven), общего вьюера
    для обоих у экосистемы нет."""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>{config.app.TITLE} — docs</title></head>
<body>
<h1>{config.app.TITLE}</h1>
<ul>
  <li><a href="/docs">REST API (OpenAPI/Swagger UI) — backend_python</a></li>
  <li><a href="{config.app.RELAY_DOCS_URL}">WebSocket protocol (AsyncAPI) — backend_go</a></li>
</ul>
</body></html>"""


async def _check_redis(redis: Redis) -> bool:
    try:
        return bool(await redis.ping())
    except Exception:
        logger.exception("healthz: redis check failed")
        return False


def _check_postgres(session: Session) -> bool:
    try:
        session.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("healthz: postgres check failed")
        return False


@router.get("/healthz")
async def healthz(
    redis: Annotated[Redis, Depends(get_redis)],
    session: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    """Readiness: проверяет сам процесс + обе зависимости, которые ему
    реально нужны для ответа на запрос (Redis — кэш/идемпотентность,
    Postgres — persistence). `SELECT 1`/`PING`, не более — цена самого
    дешёвого запроса, какой вообще существует для каждой из СУБД.
    """
    redis_ok, postgres_ok = await asyncio.gather(
        _check_redis(redis),
        asyncio.to_thread(_check_postgres, session),
    )
    healthy = redis_ok and postgres_ok
    payload = {
        "status": "ok" if healthy else "degraded",
        "checks": {
            "redis": "ok" if redis_ok else "fail",
            "postgres": "ok" if postgres_ok else "fail",
        },
    }
    return JSONResponse(payload, status_code=200 if healthy else 503)
