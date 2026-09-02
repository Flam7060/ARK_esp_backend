"""Общая инфраструктура consumer group поверх Redis Streams — переиспользуемая
для любой сущности, которую Go-сторона (`ark_relay`, будущий writer) станет
публиковать в отдельный стрим (Player — первая; Tribe/ArkStructure/TamedDino
по тому же контракту, когда дойдёт очередь).

Почему consumer group, а не голый `XREAD`: единственный читатель без группы
не переживает рестарт процесса без внешнего учёта "докуда дочитал" — группа
хранит эту позицию в самом Redis (`XREADGROUP` начиная с `>` = "новое, чего
эта группа ещё не видела"), и `XACK` — единственный протокол, которым
консьюмер говорит "обработано", отличимый от "прочитано, но упало".

Разбор конкретных полей сообщения — не здесь: этот модуль знает про
Streams, не про Player/ArkStructure/что угодно ещё.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import redis.asyncio as redis

logger = logging.getLogger(__name__)

StreamFields = dict[str, str]
Handler = Callable[[StreamFields], Awaitable[None]]


async def ensure_group(client: redis.Redis, stream: str, group: str) -> None:
    """Идемпотентно создаёт consumer group, начиная с `$` (только новые
    сообщения — не вся история стрима на первом старте). `MKSTREAM` создаёт
    сам стрим, если продюсер ещё ни разу не писал (тестовый режим "консьюмер
    раньше продюсера", см. docstring модуля)."""
    try:
        await client.xgroup_create(stream, group, id="$", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _claim_stale(
    client: redis.Redis, *, stream: str, group: str, consumer: str, min_idle_ms: int
) -> list[tuple[str, StreamFields]]:
    """Забирает сообщения, зависшие у другого (упавшего) консьюмера дольше
    `min_idle_ms` — без этого шага крах консьюмера ПОСЛЕ `XREADGROUP`, но ДО
    `XACK` теряет сообщение навсегда (никто больше его не прочитает: `>`
    отдаёт только то, что группа ещё не выдавала ни разу)."""
    _cursor, messages, _deleted = await client.xautoclaim(
        stream, group, consumer, min_idle_time=min_idle_ms, start_id="0-0", count=100
    )
    return messages


async def run_consumer(
    client: redis.Redis,
    *,
    stream: str,
    group: str,
    consumer: str,
    handler: Handler,
    stop: asyncio.Event,
    block_ms: int = 5_000,
    batch_size: int = 100,
    claim_idle_ms: int = 60_000,
) -> None:
    """Бесконечный цикл до установки `stop` — вызывающий (main.py lifespan)
    держит это в фоновой asyncio-задаче и вызывает `stop.set()` + `await
    task` на shutdown, тот же паттерн, что APScheduler в structure_flush.py,
    только без интервального опроса: `BLOCK` в `XREADGROUP` будит процесс
    сразу на новом сообщении, а не раз в N секунд.

    Ошибка в `handler` на одном сообщении не должна тушить весь консьюмер —
    сообщение просто остаётся неподтверждённым и будет забрано `_claim_stale`
    на следующего живого консьюмера (или тем же самым, после рестарта)."""
    await ensure_group(client, stream, group)

    while not stop.is_set():
        try:
            claimed = await _claim_stale(
                client, stream=stream, group=group, consumer=consumer, min_idle_ms=claim_idle_ms
            )
        except Exception:
            # Любая сетевая/протокольная ошибка здесь раньше тихо убивала
            # весь consumer навсегда: это asyncio.Task, никто не делает
            # `await` на него до shutdown, поэтому необработанное исключение
            # нигде не всплывало и не логировалось (обнаружено вживую:
            # рестарт Redis-контейнера уронил consumer без единой строки в
            # логах, `last-delivered-id` замер на месте). Ловим широко и
            # уходим на бэкофф, а не пробрасываем -- это тот самый
            # "recoverable" случай (потеря соединения, рестарт Redis), а не
            # баг в обработчике (тот уже свой except имеет в `_handle_one`).
            logger.exception("redis_stream: xautoclaim failed, retrying after backoff")
            await asyncio.sleep(5)
            continue

        for message_id, fields in claimed:
            await _handle_one(client, stream=stream, group=group, message_id=message_id, fields=fields, handler=handler)

        try:
            response = await client.xreadgroup(
                group, consumer, streams={stream: ">"}, count=batch_size, block=block_ms
            )
        except Exception:
            logger.exception("redis_stream: xreadgroup failed, retrying after backoff")
            await asyncio.sleep(5)
            continue

        for _stream_name, messages in response or []:
            for message_id, fields in messages:
                await _handle_one(
                    client, stream=stream, group=group, message_id=message_id, fields=fields, handler=handler
                )


async def _handle_one(
    client: redis.Redis, *, stream: str, group: str, message_id: str, fields: StreamFields, handler: Handler
) -> None:
    try:
        await handler(fields)
    except Exception:
        # Специально не re-raise: одно кривое сообщение не должно валить
        # консьюмер целиком. Останется unacked — заберёт _claim_stale.
        logger.exception("redis_stream: handler failed for %s %s", stream, message_id)
        return
    await client.xack(stream, group, message_id)
