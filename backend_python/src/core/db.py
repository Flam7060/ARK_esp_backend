from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.config import config

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(
            config.db.url,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=config.db.echo_sql,
        )
    return _engine


def get_session() -> Generator[Session]:
    """FastAPI-зависимость: открывает сессию и закрывает после запроса."""
    SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
