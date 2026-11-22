"""Конфигурация приложения из переменных окружения (pydantic-settings).

Состав переменных соответствует плану разработки (Таблица 4) и ТЗ.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки бота: переменные окружения или .env в корне проекта."""

    # .env подхватывается автоматически; приоритет — у настоящих переменных
    # окружения (важно для Render, где значения задаёт платформа).
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    BOT_TOKEN: str = Field(default="", description="Токен бота, выданный BotFather")
    PRIMARY_ADMIN_ID: int = Field(
        default=0, description="Telegram ID основного администратора"
    )
    BARBER_CONTACT_URL: str = Field(
        default="https://t.me/example",
        description="Ссылка на личный чат для кнопки «Связаться с барбером»",
    )
    DATABASE_PATH: str = Field(default="data/barber.db", description="Путь к файлу SQLite")
    BOOKING_HORIZON_DAYS: int = Field(
        default=30, description="Горизонт записи «на другую дату», дней"
    )
    PORT: int = Field(default=8080, description="Порт health-сервера (задаётся платформой)")
    BACKUP_REPO_URL: str = Field(
        default="", description="Приватный GitHub-репозиторий для бэкапов, формат owner/repo"
    )
    BACKUP_GH_TOKEN: str = Field(default="", description="GitHub-токен с доступом к репозиторию бэкапов")
    LOG_LEVEL: str = Field(default="INFO", description="Уровень логирования")
    TZ_NAME: str = Field(default="Europe/Moscow", description="Рабочий часовой пояс")

    @field_validator("BOOKING_HORIZON_DAYS")
    @classmethod
    def _horizon_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("BOOKING_HORIZON_DAYS должен быть положительным числом")
        return value


def get_settings() -> Settings:
    """Фабрика настроек: точка расширения для тестов."""
    return Settings()
