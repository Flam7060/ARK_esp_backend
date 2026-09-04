from __future__ import annotations

from logging import getLogger
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent  # src/core/
ENV_FILE = BASE_DIR.parent.parent / ".env"  # Backend/.env


class _Base(BaseSettings):
    """Базовый класс: читает .env-файл, лишние поля игнорирует."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AppSetting(_Base):
    """Общие параметры FastAPI-приложения."""

    TITLE: str = "Ark Backend"
    VERSION: str = "0.1.0"
    DESCRIPTION: str = "Backend API телеметрии ark_fun_tools"
    SECRET_KEY: SecretStr
    DEBUG: bool = False
    ALLOWED_HOSTS: str = "localhost,127.0.0.1"
    JWT_LIFETIME: int = 3600
    # uvicorn.run(...) — 0.0.0.0 годится для контейнера (слушать все
    # интерфейсы), но должен быть переопределяем: локальный запуск вне
    # docker без этого не забиндится на 127.0.0.1, порт занят другим
    # сервисом на хосте и т.п. — не то, что чинится правкой кода.
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    # Ссылка на AsyncAPI-докс backend_go — открывается из браузера
    # разработчика, поэтому это адрес с хоста (localhost:8081), а не
    # внутреннее docker-DNS имя "backend_go", которое браузер не резолвит.
    RELAY_DOCS_URL: str = "http://localhost:8081/docs/"

    model_config = SettingsConfigDict(env_prefix="APP_", extra="ignore")


class DatabaseSetting(_Base):
    """
    Настройки подключения к PostgreSQL.

    В docker-compose бэкенд ходит через pgpool, а не напрямую в узлы.
    Используй `db.url` как строку подключения для SQLAlchemy.

    Переменные в .env:
        DB_SCHEME   = postgresql+psycopg   (драйвер psycopg3)
        DB_HOST     = pgpool               (в Docker)
        DB_PORT     = 5432
        DB_NAME     = ark
        DB_USER     = ark
        DB_PASSWORD = <пароль>

    NAME/USER/PASSWORD — без дефолта нарочно: молчаливая подстановка
    заглушки при опечатке в имени переменной означает подключение не туда,
    куда думаешь, без единого предупреждения — лучше упасть при старте.
    """

    SCHEME: str = "postgresql+psycopg"
    HOST: str = "pgpool"
    PORT: str = "5432"
    NAME: str
    USER: str
    PASSWORD: SecretStr
    echo_sql: bool = False
    # Прямое подключение к узлу-primary (мимо балансировки pgpool) — для
    # sticky-reads: чтения сразу после записи идут на primary без лага реплики.
    PRIMARY_HOST: str = "pg-primary"
    PRIMARY_PORT: str = "5432"

    def _build_url(self, host: str, port: str) -> str:
        password = quote_plus(self.PASSWORD.get_secret_value())
        return f"{self.SCHEME}://{self.USER}:{password}@{host}:{port}/{self.NAME}"

    @property
    def url(self) -> str:
        return self._build_url(self.HOST, self.PORT)

    @property
    def async_url(self) -> str:
        """URL для async engine через pgpool (psycopg3 поддерживает async)."""
        return self._build_url(self.HOST, self.PORT)

    @property
    def primary_async_url(self) -> str:
        """URL прямого async-подключения к узлу-primary (для sticky-reads)."""
        return self._build_url(self.PRIMARY_HOST, self.PRIMARY_PORT)

    model_config = SettingsConfigDict(env_prefix="DB_", extra="ignore")


class RedisSetting(_Base):
    """Настройки Redis (кэш, очереди задач)."""

    HOST: str = "localhost"
    PORT: int = 6379
    DB: int = 0

    @property
    def url(self) -> str:
        return f"redis://{self.HOST}:{self.PORT}/{self.DB}"

    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")


class DensitySetting(_Base):
    """Параметры агрегации тепловой карты ручных дино
    (services/dino_density_service.py).

    Здесь, а не литералами в коде: размер ячейки и окно определяют, как
    выглядит карта, и подбираются на живых данных — менять их должно быть
    перезапуском, а не пересборкой.
    """

    # 10 000 юнитов = 100 м, порядок габарита базы: мельче — карта
    # рассыпается на отдельных животных, крупнее — две соседние базы
    # сливаются в одно пятно. Значение входит в уникальный ключ строки
    # (dino_density.cell_size_units), так что смена не портит старые
    # данные, а заводит новый, явно отличимый слой замеров.
    CELL_SIZE_UNITS: int = 10_000
    # Окно агрегации: строка на (ячейка, трайб, час).
    BUCKET_SECONDS: int = 3600
    # Как часто снимать замер с живого слоя. Живой слой держит сущность
    # 90с (RELAY_ENTITY_TTL), так что чаще смысла мало, а реже — начнём
    # пропускать короткие заходы скаута.
    INTERVAL_SECONDS: int = 60
    # Индекс комнаты (ZSET) релей не чистит: сущность истекает по TTL из
    # хеша, а её member в индексе остаётся. Читать весь индекс значит
    # тянуть месяцы мусора ради горстки живых, поэтому берём только
    # свежие по score (updated_at). Порог обязан быть НЕ МЕНЬШЕ
    # RELAY_ENTITY_TTL на Go-стороне (90с по умолчанию) — иначе начнём
    # терять живые сущности; с запасом на случай, если TTL поднимут.
    LIVE_FLOOR_SECONDS: int = 900

    model_config = SettingsConfigDict(env_prefix="DENSITY_", extra="ignore")


class JWTSetting(_Base):
    """
    Настройки проверки JWT телеметрии (§7.3 telemetry-api-v1.md).

    Выдача токенов — предмет отдельного AUTH-документа (§1, A1); здесь
    только то, что нужно ark_backend и ark_relay для *проверки* подписи:
    RS256, публичный ключ общий для обоих сервисов. PRIVATE_KEY_PATH нужен
    только `scripts/gen_dev_token.py` для локальной разработки — рабочий
    ark_backend его не читает.
    """

    PUBLIC_KEY_PATH: str = "../keys/jwt_public.pem"
    PRIVATE_KEY_PATH: str = "../keys/jwt_private.pem"
    LEEWAY_SECONDS: int = 5

    model_config = SettingsConfigDict(env_prefix="JWT_", extra="ignore")


class SecuritySetting(_Base):
    """
    Секрет для хеширования паролей (`admin.password_hash`, `account.password_hash`
    — см. модели, обе колонки помечены "argon2id + перец").

    Перец — второй секрет поверх соли argon2: соль хранится в самом хеше и
    ничего не даёт при утечке одной только БД, а перец живёт только в
    окружении процесса. Компрометация БД без утечки .env — недостаточна
    для офлайн-подбора паролей (obligatory: если течёт и .env, это уже не
    спасает — перец не панацея, а второй барьер, а не единственный).

    Обязателен, без дефолта: молчаливая подстановка заглушки означала бы,
    что все пароли захешированы с публично известным "секретом".
    """

    PEPPER: SecretStr

    model_config = SettingsConfigDict(env_prefix="SECURITY_", extra="ignore")


class Settings(BaseSettings):
    """
    Единая точка доступа к настройкам.

    Использование:
        from core.config import config

        db_url = config.db.url
        redis_url = config.redis.url
    """

    app: AppSetting = Field(default_factory=AppSetting)
    db: DatabaseSetting = Field(default_factory=DatabaseSetting)
    redis: RedisSetting = Field(default_factory=RedisSetting)
    jwt: JWTSetting = Field(default_factory=JWTSetting)
    security: SecuritySetting = Field(default_factory=SecuritySetting)
    density: DensitySetting = Field(default_factory=DensitySetting)

    @classmethod
    def load(cls) -> Settings:
        return cls()


config: Settings = Settings.load()
