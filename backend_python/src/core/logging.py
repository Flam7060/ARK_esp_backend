"""Базовая настройка логирования процесса — один вызов `configure_logging()`
из main.py при старте. Заменяет `logging.basicConfig`, разбросанный по
модулям (main.py, structure_flush.py, telemetry.py уже делали
`getLogger(__name__)` без общей точки конфигурации — кто первым вызвал бы
`basicConfig`, тот и задал формат всем остальным неявно).

DEBUG=True (dev): читаемая однострочная текстовая строка.
DEBUG=False (prod): JSON, одна запись — одна строка (line-delimited) —
формат, который сборщики логов (Loki/ELK/CloudWatch) читают без
regex-парсинга произвольного текста.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_configured = False

# Уровень этих логгеров понижается отдельно от общего: на INFO/DEBUG они
# генерируют служебный шум (доступ по каждому HTTP-запросу, тик планировщика)
# в разы плотнее полезных сообщений самого приложения.
_NOISY_LOGGER_LEVELS: dict[str, int] = {
    "uvicorn.access": logging.WARNING,
    "apscheduler.executors.default": logging.WARNING,
}

# Атрибуты, которые logging.LogRecord несёт всегда — используются, чтобы
# отличить "свои" поля из record.__dict__ (сообщение, уровень, ...) от
# произвольных полей, добавленных вызовом logger.info(..., extra={...}).
_RESERVED_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())


class JsonFormatter(logging.Formatter):
    """Одна запись лога — один JSON-объект в строке.

    `extra={...}`, переданный в вызов логирования, попадает в вывод как
    есть — иначе эти поля молча терялись бы (стандартный текстовый
    форматтер не печатает `extra`, если явно не прописать его в шаблоне).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and key not in payload:
                payload[key] = value

        # default=str — extra может содержать UUID/datetime/etc.; падать
        # логированием на не-JSON-сериализуемом значении хуже, чем
        # напечатать его через str().
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(*, debug: bool) -> None:
    """Настраивает root-логгер. Идемпотентна: повторный вызов (например, из
    тестового conftest.py, который может импортировать `main` не один раз
    за сессию) не плодит повторные handler'ы и не дублирует каждую строку.
    """
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    if debug:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
    else:
        handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)

    for logger_name, level in _NOISY_LOGGER_LEVELS.items():
        logging.getLogger(logger_name).setLevel(level)

    _configured = True
