import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger("bizrisk.llm")


class LLMProviderException(Exception):
    """Exception raised for LLM provider-specific errors."""
    pass


class BaseLLMProvider(ABC):
    """Base interface for all LLM providers (abstraction boundary)."""

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        schema: Type[BaseModel],
        system_instruction: Optional[str] = None,
    ) -> BaseModel:
        """Generates validated structured output matching the Pydantic schema."""
        pass


class MockLLMProvider(BaseLLMProvider):
    """Mock LLM Provider for testing, local verification, and offline runs."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        token_limit: Optional[int] = None,
        timeout: Optional[float] = None,
        retry_policy: Optional[Dict[str, Any]] = None,
    ):
        settings = get_settings()
        self.provider = provider or getattr(settings, "llm_provider", "mock")
        self.model = model or getattr(settings, "llm_model", "gemini-1.5-pro")
        
        self.temperature = (
            temperature
            if temperature is not None
            else getattr(settings, "llm_temperature", 0.0)
        )
        self.token_limit = token_limit or getattr(settings, "llm_token_limit", 4096)
        self.timeout = timeout or getattr(settings, "llm_timeout", 30.0)

        if retry_policy:
            self.retry_policy = retry_policy
        else:
            raw_policy = getattr(settings, "llm_retry_policy", None)
            if isinstance(raw_policy, str):
                try:
                    self.retry_policy = json.loads(raw_policy)
                except Exception:
                    self.retry_policy = {"max_retries": 3}
            elif isinstance(raw_policy, dict):
                self.retry_policy = raw_policy
            else:
                self.retry_policy = {"max_retries": 3}

        self._validate_config()

    def _validate_config(self):
        # Enforce TRD validation constraints
        if self.provider not in {"mock", "openai", "gemini", "anthropic"}:
            raise ValueError(f"Invalid provider: {self.provider}")
        if self.temperature < 0.0 or self.temperature > 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0. Got: {self.temperature}")
        if self.token_limit <= 0:
            raise ValueError(f"Token limit must be positive. Got: {self.token_limit}")
        if self.timeout <= 0:
            raise ValueError(f"Timeout must be positive. Got: {self.timeout}")

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[BaseModel],
        system_instruction: Optional[str] = None,
    ) -> BaseModel:
        logger.info(
            f"Generating structured output using mock provider; model={self.model}, temp={self.temperature}"
        )
        if self.provider != "mock":
            # Direct provider boundary exception if keys are unconfigured
            raise LLMProviderException(
                f"API keys and configuration for production provider '{self.provider}' are missing."
            )

        return self._generate_mock_output(schema)

    def _generate_mock_output(self, schema: Type[BaseModel]) -> BaseModel:
        # Dynamically build a valid mock instance of the Pydantic schema
        dummy_data = {}
        for name, field in schema.model_fields.items():
            field_type = field.annotation
            
            # Extract actual type if wrapped in Optional/Union
            origin = getattr(field_type, "__origin__", None)
            if origin is not None:
                # Handle List, Dict, Union, etc.
                if origin == list:
                    inner_type = field_type.__args__[0]
                    if issubclass(inner_type, BaseModel):
                        dummy_data[name] = [self._generate_mock_output(inner_type)]
                    else:
                        dummy_data[name] = []
                elif origin == dict:
                    dummy_data[name] = {}
                else:
                    dummy_data[name] = None
            elif issubclass(field_type, BaseModel):
                dummy_data[name] = self._generate_mock_output(field_type)
            elif field_type == str:
                dummy_data[name] = f"Mock {name}"
            elif field_type == int:
                dummy_data[name] = 1
            elif field_type == float:
                dummy_data[name] = 1.0
            elif field_type == bool:
                dummy_data[name] = True
            else:
                dummy_data[name] = None

        return schema(**dummy_data)


def get_llm_provider(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    token_limit: Optional[int] = None,
    timeout: Optional[float] = None,
    retry_policy: Optional[Dict[str, Any]] = None,
) -> BaseLLMProvider:
    """Factory to instantiate the appropriate configured BaseLLMProvider."""
    # Currently default to MockLLMProvider as standard
    return MockLLMProvider(
        provider=provider,
        model=model,
        temperature=temperature,
        token_limit=token_limit,
        timeout=timeout,
        retry_policy=retry_policy,
    )
