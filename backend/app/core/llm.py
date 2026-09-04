import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger("bizrisk.llm")

SUPPORTED_PROVIDERS = {"mock", "openai", "gemini", "anthropic"}

# Providers that have a concrete real implementation in this module. Anything
# supported-but-not-implemented (openai/gemini) keeps the historical behaviour of
# raising an LLMProviderException from generate_structured().
IMPLEMENTED_REAL_PROVIDERS = {"anthropic"}


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
        from app.core.tracking import llm_calls_var, token_usage_var
        from app.core.config import get_settings
        settings = get_settings()

        # Check BEFORE starting the call
        if llm_calls_var.get() >= settings.max_llm_calls:
            raise LLMProviderException("Max LLM calls limit reached")
        if token_usage_var.get() >= settings.token_budget:
            raise LLMProviderException("Token budget exhausted")

        logger.info(
            f"Generating structured output using mock provider; model={self.model}, temp={self.temperature}"
        )
        if self.provider != "mock":
            # Direct provider boundary exception if keys are unconfigured
            raise LLMProviderException(
                f"API keys and configuration for production provider '{self.provider}' are missing."
            )

        # Estimate token usage
        prompt_tokens = len(prompt) // 4
        response_tokens = 100
        total_tokens = prompt_tokens + response_tokens

        # Increment counters
        llm_calls_var.set(llm_calls_var.get() + 1)
        token_usage_var.set(token_usage_var.get() + total_tokens)

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


def _build_json_schema(schema: Type[BaseModel]) -> Dict[str, Any]:
    """Builds an Anthropic ``output_config.format`` JSON schema for a Pydantic model."""
    js = schema.model_json_schema()
    js.setdefault("additionalProperties", False)
    return js


class AnthropicLLMProvider(BaseLLMProvider):
    """
    Production LLM provider backed by the official Anthropic Python SDK.

    The SDK and API key are resolved lazily (at call time) so that importing this
    module, constructing agents, or running the mock/test path never requires the
    ``anthropic`` package or any credentials. Any provider/API failure is converted
    into :class:`LLMProviderException` so callers can fall back to deterministic
    behaviour gracefully.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        token_limit: Optional[int] = None,
        timeout: Optional[float] = None,
        retry_policy: Optional[Dict[str, Any]] = None,
        client: Any = None,
    ):
        settings = get_settings()
        self.provider = (provider or getattr(settings, "llm_provider", "anthropic")) or "anthropic"

        configured_model = model or getattr(settings, "llm_model", None)
        if not configured_model or not str(configured_model).lower().startswith("claude"):
            # The shared llm_model default targets other vendors; fall back to the
            # environment-configured Anthropic model.
            configured_model = getattr(settings, "llm_anthropic_model", "claude-opus-5")
        self.model = configured_model

        self.temperature = (
            temperature
            if temperature is not None
            else getattr(settings, "llm_temperature", 0.0)
        )
        self.token_limit = token_limit or getattr(settings, "llm_token_limit", 4096)
        self.timeout = timeout or getattr(settings, "llm_timeout", 30.0)
        self.max_retries = int(getattr(settings, "llm_max_retries", 2))

        if retry_policy:
            self.retry_policy = retry_policy
        else:
            raw_policy = getattr(settings, "llm_retry_policy", None)
            if isinstance(raw_policy, str):
                try:
                    self.retry_policy = json.loads(raw_policy)
                except Exception:
                    self.retry_policy = {"max_retries": self.max_retries}
            elif isinstance(raw_policy, dict):
                self.retry_policy = raw_policy
            else:
                self.retry_policy = {"max_retries": self.max_retries}

        self._client = client
        self._validate_config()

    def _validate_config(self):
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Invalid provider: {self.provider}")
        if self.temperature < 0.0 or self.temperature > 2.0:
            raise ValueError(f"Temperature must be between 0.0 and 2.0. Got: {self.temperature}")
        if self.token_limit <= 0:
            raise ValueError(f"Token limit must be positive. Got: {self.token_limit}")
        if self.timeout <= 0:
            raise ValueError(f"Timeout must be positive. Got: {self.timeout}")
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be non-negative. Got: {self.max_retries}")

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic  # lazy import; only needed for the real provider
        except ImportError as exc:  # pragma: no cover - depends on deploy env
            raise LLMProviderException(
                "The 'anthropic' SDK is not installed; run 'pip install anthropic'. "
                "Provider 'anthropic' is unavailable."
            ) from exc

        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY")
        if not api_key:
            raise LLMProviderException(
                "API keys and configuration for production provider 'anthropic' are missing."
            )
        self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    def _call_model(
        self,
        prompt: str,
        schema: Type[BaseModel],
        system_instruction: Optional[str],
    ) -> BaseModel:
        client = self._get_client()
        request_client = client
        with_options = getattr(client, "with_options", None)
        if callable(with_options):
            request_client = with_options(timeout=self.timeout, max_retries=self.max_retries)

        response = request_client.messages.create(
            model=self.model,
            max_tokens=self.token_limit,
            temperature=self.temperature,
            system=system_instruction
            or "You are a precise information-extraction assistant. Respond only with data that matches the requested JSON schema.",
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": _build_json_schema(schema)}},
        )

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMProviderException("Anthropic request was refused by the safety system.")

        text = None
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "text" and getattr(block, "text", None):
                text = block.text
                break
        if not text:
            raise LLMProviderException("Anthropic response contained no structured text block.")

        try:
            data = json.loads(text)
            validated = schema.model_validate(data)
        except Exception as exc:
            raise LLMProviderException(
                f"Structured output failed schema validation: {type(exc).__name__}"
            ) from exc

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        total_tokens = input_tokens + output_tokens
        if total_tokens <= 0:
            total_tokens = (len(prompt) // 4) + 200
        self._last_token_usage = total_tokens
        return validated

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[BaseModel],
        system_instruction: Optional[str] = None,
    ) -> BaseModel:
        from app.core.tracking import llm_calls_var, token_usage_var

        settings = get_settings()
        if llm_calls_var.get() >= settings.max_llm_calls:
            raise LLMProviderException("Max LLM calls limit reached")
        if token_usage_var.get() >= settings.token_budget:
            raise LLMProviderException("Token budget exhausted")

        logger.info(
            "Generating structured output using anthropic provider; model=%s, temp=%s",
            self.model,
            self.temperature,
        )

        self._last_token_usage = 0
        try:
            validated = await asyncio.wait_for(
                asyncio.to_thread(self._call_model, prompt, schema, system_instruction),
                timeout=self.timeout + 5.0,
            )
        except asyncio.TimeoutError as exc:
            raise LLMProviderException(
                f"Anthropic request timed out after {self.timeout + 5.0:.0f}s"
            ) from exc
        except LLMProviderException:
            raise
        except Exception as exc:
            # Never surface raw provider errors (which may contain request context);
            # only the exception class name is propagated.
            raise LLMProviderException(
                f"Anthropic provider call failed: {type(exc).__name__}"
            ) from exc

        llm_calls_var.set(llm_calls_var.get() + 1)
        token_usage_var.set(token_usage_var.get() + int(getattr(self, "_last_token_usage", 0) or 0))
        return validated


def get_llm_provider(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    token_limit: Optional[int] = None,
    timeout: Optional[float] = None,
    retry_policy: Optional[Dict[str, Any]] = None,
) -> BaseLLMProvider:
    """
    Factory that instantiates the configured :class:`BaseLLMProvider`.

    Selection order: explicit ``provider`` argument, else ``settings.llm_provider``
    (environment driven via ``LLM_PROVIDER``), else ``"mock"``. ``"mock"`` keeps the
    deterministic offline path used by the test-suite; ``"anthropic"`` returns the
    real provider; any other supported value (openai/gemini) keeps the historical
    not-configured boundary behaviour via :class:`MockLLMProvider`.
    """
    settings = get_settings()
    resolved_provider = (provider or getattr(settings, "llm_provider", "mock") or "mock").lower()

    if resolved_provider == "anthropic":
        try:
            return AnthropicLLMProvider(
                provider=resolved_provider,
                model=model,
                temperature=temperature,
                token_limit=token_limit,
                timeout=timeout,
                retry_policy=retry_policy,
            )
        except ValueError:
            raise
        except Exception:  # pragma: no cover - defensive; construction is cheap
            logger.exception("Failed to construct AnthropicLLMProvider; degrading to boundary provider.")
            return MockLLMProvider(
                provider="anthropic",
                model=model,
                temperature=temperature,
                token_limit=token_limit,
                timeout=timeout,
                retry_policy=retry_policy,
            )

    return MockLLMProvider(
        provider=provider,
        model=model,
        temperature=temperature,
        token_limit=token_limit,
        timeout=timeout,
        retry_policy=retry_policy,
    )


async def _await_structured(
    llm: BaseLLMProvider,
    prompt: str,
    schema: Type[BaseModel],
    system_instruction: Optional[str],
) -> BaseModel:
    return await llm.generate_structured(prompt, schema, system_instruction=system_instruction)


def run_structured_sync(
    llm: Optional[BaseLLMProvider],
    prompt: str,
    schema: Type[BaseModel],
    system_instruction: Optional[str] = None,
) -> Optional[BaseModel]:
    """
    Synchronous convenience wrapper used by the (synchronous) agent/service code to
    obtain optional LLM enrichment.

    Returns ``None`` — never raises — when:
      * ``llm`` is unset or a :class:`MockLLMProvider` / ``provider == "mock"``
        (so the deterministic mock/test path is completely unaffected), or
      * the real provider/API fails for any reason (timeout, missing key, missing
        SDK, schema-validation failure, budget exhaustion, ...).

    The caller must treat a ``None`` result as "no enrichment available" and keep
    its deterministic output. LLM output is only ever additive/advisory and never
    the authority for numeric risk scoring.
    """
    if llm is None:
        return None
    if isinstance(llm, MockLLMProvider) or getattr(llm, "provider", "mock") == "mock":
        return None

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_await_structured(llm, prompt, schema, system_instruction))

        # Already inside an event loop: run the coroutine on a private loop in a worker thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                lambda: asyncio.run(_await_structured(llm, prompt, schema, system_instruction))
            )
            return future.result()
    except LLMProviderException as exc:
        logger.warning("LLM enrichment unavailable: %s", exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive catch-all
        logger.warning("LLM enrichment failed: %s", type(exc).__name__)
        return None
