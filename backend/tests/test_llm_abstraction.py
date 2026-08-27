import pytest
from pydantic import BaseModel
from typing import List, Optional

from app.core.config import get_settings
from app.core.prompts import load_prompt
from app.core.llm import MockLLMProvider, get_llm_provider, LLMProviderException
from app.schemas.agent_outputs import IntakeOutput, PlannerOutput, ResearchTaskSchema
from app.db.base import Base

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# Restrict anyio to asyncio
@pytest.fixture
def anyio_backend():
    return "asyncio"


# Simple schema for testing structured output
class DummyOutput(BaseModel):
    name: str
    items: List[str]
    score: int
    active: bool


def test_provider_configuration_and_defaults():
    settings = get_settings()
    provider = MockLLMProvider()
    
    assert provider.provider == settings.llm_provider
    assert provider.model == settings.llm_model
    assert provider.temperature == settings.llm_temperature
    assert provider.token_limit == settings.llm_token_limit
    assert provider.timeout == settings.llm_timeout
    assert "max_retries" in provider.retry_policy


def test_provider_selection_factory():
    provider = get_llm_provider(provider="mock", model="gpt-4o", temperature=0.5)
    assert isinstance(provider, MockLLMProvider)
    assert provider.provider == "mock"
    assert provider.model == "gpt-4o"
    assert provider.temperature == 0.5


def test_invalid_provider_configuration():
    # Test invalid provider type
    with pytest.raises(ValueError, match="Invalid provider"):
        MockLLMProvider(provider="unsupported_provider")

    # Test invalid temperature (out of bounds)
    with pytest.raises(ValueError, match="Temperature must be between 0.0 and 2.0"):
        MockLLMProvider(temperature=-0.1)
    with pytest.raises(ValueError, match="Temperature must be between 0.0 and 2.0"):
        MockLLMProvider(temperature=2.5)

    # Test negative token limit
    with pytest.raises(ValueError, match="Token limit must be positive"):
        MockLLMProvider(token_limit=-100)

    # Test negative timeout
    with pytest.raises(ValueError, match="Timeout must be positive"):
        MockLLMProvider(timeout=-1.0)


@pytest.mark.anyio
async def test_structured_output_generation():
    provider = MockLLMProvider(provider="mock")
    result = await provider.generate_structured("Test prompt", DummyOutput)
    
    assert isinstance(result, DummyOutput)
    assert result.name.startswith("Mock")
    assert isinstance(result.items, list)
    assert result.score == 1
    assert result.active is True


@pytest.mark.anyio
async def test_production_provider_exception_boundary():
    # Non-mock provider raises exception when keys are missing/not configured
    provider = MockLLMProvider(provider="openai")
    with pytest.raises(LLMProviderException, match="API keys and configuration for production provider"):
        await provider.generate_structured("Test prompt", DummyOutput)


def test_prompt_version_loading():
    # Test loading existing prompt
    intake_prompt = load_prompt("intake", "v1")
    assert "Intake" in intake_prompt
    assert "Normalize" in intake_prompt

    # Test fallback loading for non-existent prompt
    fallback_prompt = load_prompt("non_existent_agent", "v99")
    assert "Default prompt for non_existent_agent version v99" in fallback_prompt


def test_report_metadata_model_prompt_tracking():
    # Import inside the test to prevent circular import on module load
    from app.models.investigation import Investigation
    from app.services.report import generate_investigation_report

    # Setup in-memory sqlite DB for test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )
    db = TestingSessionLocal()

    try:
        inv = Investigation(input_data='{"business_name": "Test Company"}')
        db.add(inv)
        db.commit()
        db.refresh(inv)

        # Generate report and specify custom prompt version
        report = generate_investigation_report(db, inv.id, prompt_version="v1")
        meta = report.get("meta") or {}

        # Verify tracking fields are registered properly in report metadata
        assert "prompt_version" in meta
        assert meta["prompt_version"]["report"] == "v1"
        assert meta["prompt_version"]["intake"] == "v1"
        assert "model_version" in meta
        assert meta["model_version"] == get_settings().llm_model
        assert "generated_at" in meta
    finally:
        db.close()
