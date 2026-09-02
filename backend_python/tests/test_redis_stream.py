"""Тесты core/redis_stream.py — реальный Redis (Streams — не то, что
осмысленно мокать: consumer-group семантика ЭТО и есть предмет теста).
Каждый тест — свой случайный stream/group, чтобы не делить состояние между
тестами (Redis не откатывается транзакцией, в отличие от db_session)."""

from __future__ import annotations

import asyncio
import uuid

import redis.asyncio as redis

from core.redis_stream import ensure_group, run_consumer


def _names() -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return f"test:stream:{suffix}", f"test:group:{suffix}"


async def _redis_client() -> redis.Redis:
    from core.config import config

    return redis.Redis.from_url(config.redis.url, decode_responses=True)


def test_ensure_group_is_idempotent(redis_available):
    async def body() -> None:
        stream, group = _names()
        client = await _redis_client()
        try:
            await ensure_group(client, stream, group)
            await ensure_group(client, stream, group)  # не должно бросить BUSYGROUP наружу
        finally:
            await client.delete(stream)
            await client.aclose()

    asyncio.run(body())


def test_run_consumer_processes_and_acks_message(redis_available):
    async def body() -> None:
        stream, group = _names()
        client = await _redis_client()
        received: list[dict[str, str]] = []

        async def handler(fields: dict[str, str]) -> None:
            received.append(fields)

        try:
            await ensure_group(client, stream, group)
            await client.xadd(stream, {"a": "1"})

            stop = asyncio.Event()
            task = asyncio.create_task(
                run_consumer(client, stream=stream, group=group, consumer="c1", handler=handler, stop=stop, block_ms=1000)
            )
            await asyncio.sleep(1.5)
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            assert received == [{"a": "1"}]
            pending = await client.xpending(stream, group)
            assert pending["pending"] == 0  # обработанное сообщение подтверждено (ACK), не висит
        finally:
            await client.delete(stream)
            await client.aclose()

    asyncio.run(body())


def test_run_consumer_leaves_failed_message_unacked_for_reclaim(redis_available):
    """Сообщение, на котором handler бросил исключение, НЕ подтверждается —
    остаётся забираемым через _claim_stale (иначе одна кривая запись тихо
    теряется навсегда)."""

    async def body() -> None:
        stream, group = _names()
        client = await _redis_client()

        async def failing_handler(fields: dict[str, str]) -> None:
            raise ValueError("boom")

        try:
            await ensure_group(client, stream, group)
            await client.xadd(stream, {"a": "1"})

            stop = asyncio.Event()
            task = asyncio.create_task(
                run_consumer(
                    client, stream=stream, group=group, consumer="c1", handler=failing_handler, stop=stop, block_ms=1000
                )
            )
            await asyncio.sleep(1.5)
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            pending = await client.xpending(stream, group)
            assert pending["pending"] == 1  # НЕ подтверждено — доступно для повторной обработки
        finally:
            await client.delete(stream)
            await client.aclose()

    asyncio.run(body())


def test_run_consumer_reclaims_stale_message_from_crashed_consumer(redis_available):
    """Сообщение, выданное consumer'у, который упал ДО XACK, должно быть
    подобрано следующим запуском (claim_idle_ms=0 — забирать сразу, не ждать
    реальной минуты простоя, тест не обязан столько ждать)."""

    async def body() -> None:
        stream, group = _names()
        client = await _redis_client()
        received: list[dict[str, str]] = []

        async def handler(fields: dict[str, str]) -> None:
            received.append(fields)

        try:
            await ensure_group(client, stream, group)
            await client.xadd(stream, {"a": "1"})
            # Имитация упавшего консьюмера: сам прочитал (XREADGROUP), но
            # никогда не подтвердил и не переспросил — сообщение "зависло".
            await client.xreadgroup(group, "dead-consumer", streams={stream: ">"}, count=1)

            stop = asyncio.Event()
            task = asyncio.create_task(
                run_consumer(
                    client,
                    stream=stream,
                    group=group,
                    consumer="rescuer",
                    handler=handler,
                    stop=stop,
                    block_ms=1000,
                    claim_idle_ms=0,
                )
            )
            await asyncio.sleep(1.5)
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            assert received == [{"a": "1"}]
        finally:
            await client.delete(stream)
            await client.aclose()

    asyncio.run(body())
