"""Общие фикстуры для интеграционных тестов бэкенда.

Тесты бьют по настоящему Postgres (не sqlite/mock) — модели используют
`postgresql.UUID`, диалект-специфичный тип; подменять его моком ради
скорости значит тестировать не ту БД, на которой реально работает прод.
Адрес берётся из той же core.config, что читает и само приложение (DB_HOST
и т.д.) — подними `docker compose up postgres redis` рядом или укажи свой
Postgres теми же переменными окружения перед запуском `pytest`.

Если Postgres недоступен, фикстура падает `pytest.skip` с понятной
причиной — не голым traceback от psycopg где-то в середине теста.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def _seed_code_lookups(engine) -> None:
    """`create_all` строит только схему, не данные — те же строки, что
    сеет Alembic-миграция `93868d516e83_add_auth_and_ark_schema.py` для
    прода, здесь нужны тестам напрямую (FK на *_code иначе не пройдёт ни
    один insert с default'ным role_code/status_code/origin_code)."""
    from sqlalchemy.orm import Session as _Session

    from models.auth_lookups import (
        AccountStatus,
        ActivationKeyOrigin,
        ActivationKeyStatus,
        AdminRole,
        AdminStatus,
        ApiKeyStatus,
        GroupRole,
        InviteStatus,
    )

    rows = [
        AdminRole(code="superadmin", label="Суперадминистратор"),
        AdminRole(code="admin", label="Администратор"),
        AdminRole(code="support", label="Поддержка"),
        AdminRole(code="developer", label="Разработчик"),
        AdminStatus(code="active", label="Активен"),
        AdminStatus(code="disabled", label="Отключён"),
        AdminStatus(code="locked", label="Заблокирован"),
        AccountStatus(code="active", label="Активен"),
        AccountStatus(code="suspended", label="Приостановлен"),
        ActivationKeyOrigin(code="purchase", label="Покупка"),
        ActivationKeyOrigin(code="invite", label="Приглашение"),
        ActivationKeyOrigin(code="gift", label="Подарок"),
        ActivationKeyStatus(code="issued", label="Выдан", is_terminal=False),
        ActivationKeyStatus(code="redeemed", label="Погашен", is_terminal=True),
        ApiKeyStatus(code="active", label="Активен", is_terminal=False),
        ApiKeyStatus(code="revoked", label="Отозван", is_terminal=True),
        GroupRole(code="owner", label="Владелец"),
        GroupRole(code="member", label="Участник"),
        InviteStatus(code="active", label="Активно"),
        InviteStatus(code="revoked", label="Отозвано"),
        InviteStatus(code="expired", label="Истекло"),
    ]
    with _Session(engine) as session:
        session.add_all(rows)
        session.commit()


@pytest.fixture(scope="session")
def db_engine():
    from core.config import config as app_config
    from models.base import Base
    import models  # noqa: F401 — регистрирует все модели в Base.metadata

    engine = create_engine(app_config.db.url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — любая причина недоступности БД одинаково ведёт к skip
        pytest.skip(f"Postgres недоступен по {app_config.db.HOST}:{app_config.db.PORT}: {exc}")

    Base.metadata.create_all(engine)
    _seed_code_lookups(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Iterator[Session]:
    """Одна сессия на тест, откатывается после — тесты не видят данные
    друг друга, БД не нужно чистить руками между прогонами."""
    connection = db_engine.connect()
    transaction = connection.begin()
    # join_transaction_mode="create_savepoint": сервисный слой сам вызывает
    # session.commit()/rollback() (см. services/user_service.py) — без этого
    # его rollback() после IntegrityError закрыл бы саму внешнюю transaction,
    # и rollback() фикстуры на выходе упал бы в уже мёртвую транзакцию.
    # Savepoint даёт commit/rollback сервису, не трогая внешнюю транзакцию.
    session_factory = sessionmaker(
        bind=connection, autocommit=False, autoflush=False, join_transaction_mode="create_savepoint"
    )
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def redis_available() -> None:
    """Skip-гейт для тестов, которым нужен настоящий Redis (Streams — не то,
    что осмысленно мокать: семантика consumer group — часть контракта, а не
    деталь реализации). Та же логика, что db_engine, но синхронным клиентом
    — тесты сами дальше поднимают `redis.asyncio`, эта фикстура только
    решает, стоит ли вообще пытаться."""
    import redis as sync_redis

    from core.config import config as app_config

    client = sync_redis.Redis.from_url(app_config.redis.url)
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001 — любая причина недоступности одинаково ведёт к skip
        pytest.skip(f"Redis недоступен по {app_config.redis.HOST}:{app_config.redis.PORT}: {exc}")
    finally:
        client.close()


@pytest.fixture()
def client(db_session: Session):
    """TestClient с get_session, подменённым на db_session — тест видит
    те же изменения через HTTP, что делает сервисный слой напрямую, и всё
    внутри одной откатываемой транзакции."""
    from fastapi.testclient import TestClient

    from core.db import get_session
    from main import app

    def _override_get_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        # `with` запускает lifespan (main.lifespan) — тест проходит тот же
        # startup/shutdown, что и реальный процесс, а не urlsafe-заглушку.
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)
