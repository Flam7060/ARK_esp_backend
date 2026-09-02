"""End-to-end: XADD в реальный Redis Stream -> run_consumer -> строка в
Postgres. Единственный тест, проверяющий весь путь целиком, а не слой по
отдельности (те — test_redis_stream.py, test_player_ingest_service.py).

Консьюмер открывает СВОЮ сессию через sessionmaker(bind=db_engine) — не
db_session-фикстуру (та в отдельной, откатываемой транзакции) — как и в
проде (services.player_ingest_service.make_handler). Поэтому строки чистим
руками в finally, а не полагаемся на автоматический rollback db_session.
"""

from __future__ import annotations

import asyncio
import uuid

import redis.asyncio as redis
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from core.redis_stream import ensure_group, run_consumer
from models.ark_lookups import GameMap
from models.player import Player
from models.topology import Person, Server
from services.player_ingest_service import make_handler


def test_xadd_message_is_ingested_into_postgres(redis_available, db_engine):
    from core.config import config

    stream = f"test:player_sighting:{uuid.uuid4().hex[:8]}"
    group = f"test:player_ingest:{uuid.uuid4().hex[:8]}"
    platform_id = f"steam:e2e-{uuid.uuid4().hex[:8]}"

    own_session_factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    with own_session_factory() as setup_session:
        setup_session.add(GameMap(code=f"map-{uuid.uuid4().hex[:6]}", name="E2E Map"))
        setup_session.flush()
        map_code = setup_session.execute(select(GameMap.code)).scalars().first()
        server = Server(name="E2E Server", map_code=map_code)
        setup_session.add(server)
        setup_session.commit()
        server_id = server.id

    async def body() -> None:
        client = redis.Redis.from_url(config.redis.url, decode_responses=True)
        try:
            await ensure_group(client, stream, group)
            await client.xadd(
                stream,
                {
                    "server_id": str(server_id),
                    "platform_id": platform_id,
                    "character_name": "E2ERex",
                    "level": "7",
                    "x": "1.1",
                    "y": "2.2",
                    "z": "3.3",
                },
            )

            stop = asyncio.Event()
            task = asyncio.create_task(
                run_consumer(
                    client,
                    stream=stream,
                    group=group,
                    consumer="e2e-test",
                    handler=make_handler(own_session_factory),
                    stop=stop,
                    block_ms=1000,
                )
            )
            await asyncio.sleep(1.5)
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            await client.delete(stream)
            await client.aclose()

    try:
        asyncio.run(body())

        with own_session_factory() as check_session:
            player = check_session.execute(
                select(Player).where(Player.server_id == server_id, Player.platform_id == platform_id)
            ).scalar_one()
            assert player.character_name == "E2ERex"
            assert player.level == 7
            assert player.x == 1.1
    finally:
        with own_session_factory() as cleanup_session:
            cleanup_session.execute(delete(Player).where(Player.server_id == server_id))
            cleanup_session.execute(delete(Person).where(Person.platform_id == platform_id))
            cleanup_session.execute(delete(Server).where(Server.id == server_id))
            cleanup_session.commit()
