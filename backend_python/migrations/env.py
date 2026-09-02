from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# prepend_sys_path=%(here)s/src в alembic.ini уже положил src/ в sys.path —
# отсюда можно импортировать модули приложения напрямую, как это делает
# сам FastAPI-процесс.
from core.config import config as app_config
from models.base import Base
import models  # noqa: F401 — регистрирует все ORM-модели в Base.metadata (models/__init__.py)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Один источник правды для URL — core.config, тот же, что читает
# приложение. Не дублируем креды второй раз в alembic.ini.
config.set_main_option("sqlalchemy.url", app_config.db.url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
