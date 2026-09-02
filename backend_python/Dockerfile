# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.5-python3.11-bookworm-slim AS builder
WORKDIR /app

# Зависимости отдельным слоем, до копирования src — правка кода не тянет
# переустановку пакетов.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.11-slim-bookworm
WORKDIR /app
COPY --from=builder /app /app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
# scripts/ — CLI-тулы вне HTTP-поверхности (create_admin.py и т.п.),
# запускаются через `docker compose exec`, не часть самого приложения —
# но без этого COPY их просто нет в образе, exec упал бы "No such file".
COPY scripts ./scripts
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1
WORKDIR /app/src

EXPOSE 8000
# ENTRYPOINT прогоняет `alembic upgrade head`, потом exec CMD — миграции
# идут при каждом старте контейнера, а не отдельным ручным шагом, который
# кто-нибудь однажды забудет выполнить перед деплоем.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
