from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общий declarative base для всех ORM-моделей — только модели (и то,
    что читает их метаданные: миграции, тестовый create_all) на него и
    ссылаются. core/db.py про открытие соединения (Engine/Session) и сам
    Base не использует — разные ответственности, не должны были жить в
    одном файле."""
