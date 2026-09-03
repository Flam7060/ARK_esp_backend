#!/usr/bin/env bash
# Полный снос стека -- обратно в состояние чистого VPS (как до первого
# deploy.sh). ДЕСТРУКТИВНО и НЕОБРАТИМО:
#   - контейнеры + volumes (Postgres -- все аккаунты/группы/ключи пропадут)
#   - локально собранные образы
#   - .env (DB_PASSWORD, APP_SECRET_KEY, SECURITY_PEPPER)
#   - keys/ (JWT RS256 keypair -- инвалидирует все выданные токены; QUIC
#     TLS keypair)
#
# Для мягкой очистки (код/образы, БД и секреты остаются) — make clean,
# не этот скрипт.
#
# Usage:
#   cd backend && ./deploy/purge.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

log()  { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

[[ -f docker-compose.yml ]] || die "run this from the backend/ checkout (docker-compose.yml not found next to deploy/)"

warn "Это снесёт БЕЗВОЗВРАТНО:"
warn "  - все контейнеры и volumes (Postgres -- аккаунты, группы, всё)"
warn "  - локально собранные образы (backend_python, backend_go)"
warn "  - .env (пароль БД, секреты приложения)"
warn "  - keys/ (JWT-кейпара -- инвалидирует все выданные токены; QUIC-кейпара)"
echo
read -rp "Введи PURGE заглавными, чтобы подтвердить: " confirm
[[ "$confirm" == "PURGE" ]] || die "отменено (ожидалось PURGE, получено: ${confirm:-<пусто>})"

log "Останавливаю и сношу контейнеры, volumes, локальные образы"
docker compose down -v --rmi local --remove-orphans

log "Удаляю .env и keys/"
rm -f .env
rm -rf keys

log "Готово -- состояние как у чистого VPS. Следующий деплой: ./deploy/deploy.sh"
