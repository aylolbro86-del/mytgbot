from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    openai_api_key: str
    db_url: str = "sqlite+aiosqlite:///bot_database.sqlite"

    # Контекст диалога
    memory_window: int = 6

    # AI
    ai_model: str = "deepseek/deepseek-chat"
    ai_temperature: float = 0.7
    ai_max_tokens: int = 1024
    ai_base_url: str = "https://api.vsegpt.ru/v1"
    ai_timeout: int = 45
    ai_max_concurrent: int = 5  # Макс параллельных запросов к AI

    # Лимиты
    max_message_length: int = 300
    free_daily_limit: int = 5
    rate_limit_seconds: int = 3
    max_tracked_messages: int = 50

    # Админы (через запятую: ADMIN_IDS=123456789,987654321)
    admin_ids: str = ""

    @property
    def admin_list(self) -> list[int]:
        if not self.admin_ids:
            return []
        return [int(x.strip()) for x in self.admin_ids.split(",") if x.strip().isdigit()]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


config = Settings()
