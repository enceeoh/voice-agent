from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven config. Locally from .env; in prod from the platform."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str
    deepgram_api_key: str
    database_url: str = "postgresql://voice:voice@localhost:5433/voice"

    anthropic_model: str = "claude-opus-4-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
