# Обёртка над `docker compose` для частых команд разработки, плюс сам
# bring-up/tear-down (deploy/deploy.sh, deploy/purge.sh) — те же шорткаты
# поверх docker-compose.yml, которым и так пользуется прод-запуск.
#
# ARGS пробрасывается как есть в команды, принимающие произвольные флаги
# (create-admin, alembic, exec): `make create-admin ARGS="--username root"`.
# MSG — отдельно для make-migration, чтобы не путать текст сообщения
# ревизии с обычными флагами команды.

COMPOSE       := docker compose
PYTHON_SVC    := backend_python
GO_SVC        := backend_go
DB_SVC        := postgres
REDIS_SVC     := redis

.PHONY: help deploy up down restart build rebuild rebuild-python rebuild-go ps \
        logs logs-python logs-go logs-db logs-redis \
        migrate migrate-status make-migration create-admin \
        shell-python shell-go psql redis-cli \
        test down-v clean purge

help: ## Список команд с описанием
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

## --- Жизненный цикл стека ---

deploy: ## Развернуть с нуля на свежем VPS (deploy/deploy.sh) — генерит .env/ключи если их ещё нет, поднимает стек. Идемпотентно, безопасно перезапускать после git pull
	./deploy/deploy.sh

up: ## Поднять стек в фоне НА УЖЕ СОБРАННЫХ образах — правил код/Dockerfile? нужен make rebuild, не up
	$(COMPOSE) up -d

down: ## Остановить и убрать контейнеры (volume с данными Postgres остаётся)
	$(COMPOSE) down

down-v: ## Как down, но ещё и стирает volume с данными Postgres — ДЕСТРУКТИВНО
	$(COMPOSE) down -v

clean: ## Снести контейнеры и локально собранные образы — БД (volume), .env, keys/ остаются нетронуты. Для чистого редеплоя кода без потери данных/секретов
	$(COMPOSE) down --rmi local --remove-orphans

purge: ## ПОЛНЫЙ снос: контейнеры, volumes (БД), образы, .env, keys/ — обратно в состояние чистого VPS. НЕОБРАТИМО, спросит подтверждение (deploy/purge.sh)
	./deploy/purge.sh

restart: ## Перезапустить только backend_python (миграции прогонятся заново при старте)
	$(COMPOSE) restart $(PYTHON_SVC)

build: ## Пересобрать образы БЕЗ перезапуска — контейнеры продолжат работать на старом образе, пока не пересоздашь (см. rebuild)
	$(COMPOSE) build

rebuild: ## Пересобрать образы И пересоздать контейнеры — то, что реально нужно после правки Dockerfile/scripts/pyproject.toml
	$(COMPOSE) up -d --build

rebuild-python: ## Как rebuild, но только backend_python (не трогает postgres/redis/backend_go)
	$(COMPOSE) up -d --build $(PYTHON_SVC)

rebuild-go: ## Как rebuild, но только backend_go
	$(COMPOSE) up -d --build $(GO_SVC)

ps: ## Статус контейнеров стека
	$(COMPOSE) ps

## --- Логи ---

logs: ## Логи всех сервисов (follow)
	$(COMPOSE) logs -f --tail=200

logs-python: ## Логи backend_python (follow) — JSON-строки, см. core/logging.py
	$(COMPOSE) logs -f --tail=200 $(PYTHON_SVC)

logs-go: ## Логи backend_go / ark_relay (follow)
	$(COMPOSE) logs -f --tail=200 $(GO_SVC)

logs-db: ## Логи Postgres (follow)
	$(COMPOSE) logs -f --tail=200 $(DB_SVC)

logs-redis: ## Логи Redis (follow)
	$(COMPOSE) logs -f --tail=200 $(REDIS_SVC)

## --- Миграции (Alembic) ---

migrate: ## Прогнать миграции вручную (обычно не нужно — entrypoint делает это при каждом старте)
	$(COMPOSE) exec $(PYTHON_SVC) alembic upgrade head

migrate-status: ## Текущая ревизия БД + сравнение с models/* (детектит расхождение схемы)
	$(COMPOSE) exec $(PYTHON_SVC) alembic current
	$(COMPOSE) exec $(PYTHON_SVC) alembic check

make-migration: ## Сгенерировать новую ревизию: make make-migration MSG="add foo"
	@test -n "$(MSG)" || (echo 'error: укажи MSG="описание ревизии"' >&2; exit 1)
	$(COMPOSE) exec $(PYTHON_SVC) alembic revision --autogenerate -m "$(MSG)"

## --- Прикладные CLI-тулы ---

create-admin: ## Создать admin-учётку: make create-admin ARGS="--username root --role superadmin"
	$(COMPOSE) exec $(PYTHON_SVC) python /app/scripts/create_admin.py $(ARGS)

## --- Доступ внутрь контейнеров ---

shell-python: ## Шелл внутри backend_python
	$(COMPOSE) exec $(PYTHON_SVC) /bin/sh

shell-go: ## Шелл внутри backend_go (образ distroless — обычно не работает, оставлено для не-distroless сборок)
	$(COMPOSE) exec $(GO_SVC) /bin/sh

psql: ## psql к рабочей БД (креды берёт из окружения самого контейнера postgres)
	$(COMPOSE) exec $(DB_SVC) sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

redis-cli: ## redis-cli к рабочему Redis
	$(COMPOSE) exec $(REDIS_SVC) redis-cli

## --- Тесты (локально, не в контейнере) ---

test: ## pytest backend_python против postgres/redis из compose (подними их: make up)
	cd backend_python && uv run python -m pytest $(ARGS)
