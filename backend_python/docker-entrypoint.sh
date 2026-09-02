#!/bin/sh
# Прогоняет миграции перед стартом процесса — если alembic upgrade
# упадёт (недоступна БД, конфликт ревизий), `set -e` не даёт uvicorn
# подняться поверх незавершённой/сломанной схемы.
set -e

alembic -c /app/alembic.ini upgrade head

exec "$@"
