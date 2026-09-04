from functools import lru_cache
from typing import Any

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
    # Real-provider configuration (environment driven). The API key itself is never
    # stored here; the Anthropic provider reads ANTHROPIC_API_KEY / LLM_API_KEY from
    # the environment at call time so it cannot leak through a Settings repr.
    llm_anthropic_model: str = "claude-opus-5"
    llm_max_retries: int = 2

    evidence_freshness_gst_days: int = 7
    evidence_freshness_mca_days: int = 30
    evidence_freshness_website_days: int = 30
    evidence_freshness_default_days: int = 30

    max_research_depth: int = 3
    max_browser_actions: int = 20
    max_research_tasks: int = 15
    max_llm_calls: int = 50
    token_budget: int = 100000

    # Entity-resolution acceptance threshold (0.0-1.0). A resolved match at or
    # above this similarity score proceeds to risk analysis; below it the
    # investigation loops back to the planner for additional research.
    entity_resolution_threshold: float = 0.75
    # Maximum number of QA -> planner correction loops before an investigation is
    # released as PARTIALLY_COMPLETED / FAILED.
    max_qa_loops: int = 2
    # Gate the unauthenticated /api/v1/test/* agent-inspection endpoints. Must be
    # false in a production deployment.
    enable_test_endpoints: bool = True

    cors_origins: list[str] = ["http://localhost:3000"]
    playwright_headless: bool = True

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, v: Any) -> Any:
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return "postgresql+psycopg://" + v[11:]
            if v.startswith("postgresql://"):
                return "postgresql+psycopg://" + v[13:]
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator(
        "max_research_depth",
        "max_browser_actions",
        "max_research_tasks",
        "max_llm_calls",
        "token_budget",
        "max_qa_loops",
        mode="after",
    )
    @classmethod
    def validate_positive_limits(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Limit must be non-negative")
        return v

    @field_validator("entity_resolution_threshold", mode="after")
    @classmethod
    def validate_resolution_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("entity_resolution_threshold must be between 0.0 and 1.0")
        return v

    def model_post_init(self, __context: Any) -> None:
        if self.environment.lower() in ("production", "prod"):
            if self.debug:
                raise ValueError("debug must be False in production environment")
            if not self.database_url:
                raise ValueError("database_url is required in production environment")
            if self.enable_test_endpoints:
                raise ValueError(
                    "enable_test_endpoints must be False in production environment"
                )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

