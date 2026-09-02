from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import redis.asyncio as redis
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from core.config import config
from core.db import get_engine
from core.logging import configure_logging
from core.redis import get_pool
from core.redis_stream import Handler, run_consumer
from routers.v1 import (
    account_auth,
    accounts,
    activation_keys,
    admin_accounts,
    admin_auth,
    api_keys,
    groups,
    status,
    telemetry,
    tribes,
)
from services import dino_sighting_service, player_ingest_service, structure_sighting_service
from services.structure_flush import start_scheduler

configure_logging(debug=config.app.DEBUG)
logger = logging.getLogger(__name__)


@dataclass
class _StreamConsumer:
    """Один Redis-клиент + одна фоновая задача на consumer group — три
    ingestion-стрима (player/structure/dino) повторяют один и тот же
    жизненный цикл дословно, вынесено сюда после того, как третья копия
    сделала повторение заметнее разницы между ними."""

    redis_client: redis.Redis
    stop: asyncio.Event
    task: asyncio.Task[None]


def _start_stream_consumer(
    *, stream: str, group: str, make_handler: Callable[[Callable[[], Session]], Handler]
) -> _StreamConsumer:
    """Свой Redis-клиент на consumer (blocking XREADGROUP не должен делить
    соединение с чем-то ещё) и свой session_factory (Session не
    потокобезопасна, каждый апсерт открывает свою через asyncio.to_thread —
    см. services/player_ingest_service.make_handler)."""
    client = redis.Redis(connection_pool=get_pool())
    session_factory = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_consumer(
            client,
            stream=stream,
            group=group,
            consumer=f"ark_backend-{uuid.uuid4().hex[:8]}",
            handler=make_handler(session_factory),
            stop=stop,
        )
    )
    return _StreamConsumer(redis_client=client, stop=stop, task=task)


async def _stop_stream_consumer(consumer: _StreamConsumer) -> None:
    consumer.stop.set()
    consumer.task.cancel()  # будит блокирующий XREADGROUP немедленно, не ждёт до block_ms
    try:
        await consumer.task
    except asyncio.CancelledError:
        pass
    await consumer.redis_client.aclose()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Один клиент на весь процесс для фоновой задачи (не HTTP-запрос —
    # core.redis.get_redis не подходит, та зависимость привязана к
    # жизненному циклу запроса).
    scheduler_redis = redis.Redis(connection_pool=get_pool())
    scheduler = start_scheduler(scheduler_redis)

    consumers = [
        _start_stream_consumer(
            stream=player_ingest_service.STREAM_NAME,
            group=player_ingest_service.GROUP_NAME,
            make_handler=player_ingest_service.make_handler,
        ),
        _start_stream_consumer(
            stream=structure_sighting_service.STREAM_NAME,
            group=structure_sighting_service.GROUP_NAME,
            make_handler=structure_sighting_service.make_handler,
        ),
        _start_stream_consumer(
            stream=dino_sighting_service.STREAM_NAME,
            group=dino_sighting_service.GROUP_NAME,
            make_handler=dino_sighting_service.make_handler,
        ),
    ]

    logger.info("ark_backend started")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await scheduler_redis.aclose()

        for consumer in consumers:
            await _stop_stream_consumer(consumer)

        logger.info("ark_backend stopped")


# Порядок здесь определяет порядок разделов в Swagger UI — держим его тем
# же, что и порядок include_router ниже, чтобы связанные ручки (сперва
# логин, потом то, что этим логином защищено) не скакали местами.
_OPENAPI_TAGS = [
    {"name": "status", "description": "Технические ручки процесса: HTML-портал доков, readiness-healthcheck."},
    {
        "name": "Admin Auth",
        "description": "Логин внутренней админки. Учётки заводятся только CLI (scripts/create_admin.py) — "
        "self-signup эндпоинта нет и не будет.",
    },
    {
        "name": "Activation Keys",
        "description": "Ключи активации подписки — CRUD только для аутентифицированных админов "
        "(Authorization: Bearer <admin-токен из Admin Auth>).",
    },
    {
        "name": "Accounts",
        "description": "Регистрация сервисного account'а по ключу активации, смена пароля, подтверждение "
        "сброса пароля — публично или с account-токеном, где отмечено.",
    },
    {
        "name": "Account Auth",
        "description": "Логин account'а (FR-052). Отдельно от Admin Auth — разные субъекты, разные JWT.",
    },
    {
        "name": "Admin Accounts",
        "description": "Админ-операции над чужими account (сейчас — выпуск токена сброса пароля; "
        "сам новый пароль админ не видит и не задаёт).",
    },
    {
        "name": "API Keys",
        "description": "Self-service API-ключи account'а — Authorization: Bearer <account-токен>.",
    },
    {
        "name": "Groups",
        "description": "Группы шаринга: создание, приглашения, вступление, участники — "
        "Authorization: Bearer <account-токен>.",
    },
]

app = FastAPI(
    title=config.app.TITLE,
    version=config.app.VERSION,
    description=config.app.DESCRIPTION,
    debug=config.app.DEBUG,
    lifespan=lifespan,
    openapi_tags=_OPENAPI_TAGS,
)

app.include_router(status.router)
app.include_router(admin_auth.router)
app.include_router(activation_keys.router)
app.include_router(account_auth.router)
app.include_router(accounts.router)
app.include_router(admin_accounts.router)
app.include_router(api_keys.router)
app.include_router(groups.router)
app.include_router(telemetry.router)
app.include_router(tribes.router)


def main() -> None:
    import uvicorn

    uvicorn.run("main:app", host=config.app.HOST, port=config.app.PORT, reload=config.app.DEBUG)


if __name__ == "__main__":
    main()
