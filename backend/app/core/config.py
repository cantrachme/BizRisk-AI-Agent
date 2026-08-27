from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "BizRisk AI Agent"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True

    database_url: str = "postgresql+psycopg://bizrisk:bizrisk@localhost:5432/bizrisk"
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: str = "mock"
    llm_model: str = "gemini-1.5-pro"
    llm_temperature: float = 0.0
    llm_token_limit: int = 4096
    llm_timeout: float = 30.0
    llm_retry_policy: str = '{"max_retries": 3, "backoff_factor": 2}'

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
