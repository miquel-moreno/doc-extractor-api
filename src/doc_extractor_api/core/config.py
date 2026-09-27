"""Application settings, read from environment variables (and .env)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "doc-extractor-api"
    log_level: str = "INFO"

    llm_provider: Literal["openai", "anthropic", "ollama"] = "ollama"
    llm_model: str = "llama3.2"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/app"


@lru_cache
def get_settings() -> Settings:
    return Settings()
