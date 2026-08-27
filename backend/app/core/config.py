from functools import lru_cache

from pydantic import field_validator
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

    evidence_freshness_gst_days: int = 7
    evidence_freshness_mca_days: int = 30
    evidence_freshness_website_days: int = 30
    evidence_freshness_default_days: int = 30

    max_research_depth: int = 3
    max_browser_actions: int = 20
    max_research_tasks: int = 15
    max_llm_calls: int = 50
    token_budget: int = 100000

    @field_validator(
        "max_research_depth",
        "max_browser_actions",
        "max_research_tasks",
        "max_llm_calls",
        "token_budget",
        mode="after",
    )
    @classmethod
    def validate_positive_limits(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Limit must be non-negative")
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
