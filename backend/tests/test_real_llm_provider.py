"""
Focused tests for the real (Anthropic) LLM provider integration.

Covers exactly the four areas required for this change:
  1. provider selection / factory behaviour
  2. strict structured-output schema validation
  3. timeout & graceful provider/API failure handling
  4. the invariant that the LLM can never determine the final numerical risk
     score / risk level (the deterministic Risk Engine remains the sole authority)

The mock/offline path used by the rest of the suite is untouched and is
re-asserted here as a guard.
"""

import asyncio
import inspect
import json
from typing import List, Optional

import pytest
from pydantic import BaseModel

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import llm as llm_mod
from app.core.llm import (
    AnthropicLLMProvider,
    LLMProviderException,
    MockLLMProvider,
    get_llm_provider,
    run_structured_sync,
)
from app.core.config import get_settings
from app.core.tracking import llm_calls_var, token_usage_var
from app.db.base import Base
from app.graph.state import ResearchResult
from app.models.investigation import Investigation
from app.schemas.agent_outputs import QAReasoning, ReportNarrative
from app.services.evidence import get_evidences_for_investigation, save_research_result
from app.services.report import generate_investigation_report
from app.services.qa import validate_report
from app.services.risk_analysis import analyze_investigation
from app.risk.engine import calculate_risk_analysis
from app.risk.rules import run_all_rules


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_tracking():
    llm_calls_var.set(0)
    token_usage_var.set(0)
    yield
    llm_calls_var.set(0)
    token_usage_var.set(0)


# --------------------------------------------------------------------------- #
# Fakes: a stand-in for anthropic.Anthropic so no SDK / network is required
# --------------------------------------------------------------------------- #
class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    def __init__(self, input_tokens=13, output_tokens=9):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Resp:
    def __init__(self, text=None, blocks=None, stop_reason="end_turn"):
        self.content = blocks if blocks is not None else [_Block(text)]
        self.usage = _Usage()
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, behavior):
        self._behavior = behavior
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if callable(self._behavior):
            return self._behavior(**kwargs)
        return self._behavior


class FakeAnthropicClient:
    """Mimics the small slice of anthropic.Anthropic the provider uses."""

    def __init__(self, behavior):
        self.messages = _FakeMessages(behavior)
        self.with_options_calls = []

    def with_options(self, **kwargs):
        self.with_options_calls.append(kwargs)
        return self


class SampleSchema(BaseModel):
    name: str
    items: List[str] = []
    note: Optional[str] = None


def _provider(behavior, **kwargs):
    return AnthropicLLMProvider(
        provider="anthropic",
        client=FakeAnthropicClient(behavior),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# 1. Provider selection
# --------------------------------------------------------------------------- #
def test_factory_defaults_to_mock():
    provider = get_llm_provider()
    assert isinstance(provider, MockLLMProvider)
    assert provider.provider == "mock"


def test_factory_explicit_mock_is_unchanged():
    provider = get_llm_provider(provider="mock", model="gpt-4o", temperature=0.5)
    assert isinstance(provider, MockLLMProvider)
    assert provider.provider == "mock"
    assert provider.model == "gpt-4o"
    assert provider.temperature == 0.5


def test_factory_selects_anthropic_when_requested():
    provider = get_llm_provider(provider="anthropic")
    assert isinstance(provider, AnthropicLLMProvider)
    assert provider.provider == "anthropic"
    # The shared llm_model default targets another vendor -> coerced to the
    # environment-configured Anthropic model.
    assert provider.model.startswith("claude")


def test_factory_is_env_driven(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_provider", "anthropic")
    provider = get_llm_provider()  # no explicit provider arg
    assert isinstance(provider, AnthropicLLMProvider)


def test_anthropic_model_is_configurable_and_respects_claude_ids():
    assert _provider(_Resp("{}")).model.startswith("claude")
    assert _provider(_Resp("{}"), model="claude-sonnet-5").model == "claude-sonnet-5"
    # non-claude string is replaced with the configured Anthropic default
    assert _provider(_Resp("{}"), model="gemini-1.5-pro").model == "claude-opus-5"


@pytest.mark.anyio
async def test_unimplemented_provider_keeps_historical_boundary():
    # openai/gemini stay "supported but not configured" and raise from the call.
    provider = get_llm_provider(provider="gemini")
    with pytest.raises(
        LLMProviderException,
        match="API keys and configuration for production provider",
    ):
        await provider.generate_structured("prompt", SampleSchema)


def test_anthropic_provider_rejects_invalid_numeric_config():
    with pytest.raises(ValueError, match="Temperature must be between"):
        _provider(_Resp("{}"), temperature=3.0)
    with pytest.raises(ValueError, match="Timeout must be positive"):
        _provider(_Resp("{}"), timeout=-1.0)
    with pytest.raises(ValueError, match="Token limit must be positive"):
        _provider(_Resp("{}"), token_limit=-100)


# --------------------------------------------------------------------------- #
# 2. Strict structured output / schema validation
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_valid_structured_output_is_parsed_and_tracked():
    payload = {"name": "ACME Foods Pvt Ltd", "items": ["gstin", "cin"], "note": None}
    provider = _provider(_Resp(json.dumps(payload)))

    result = await provider.generate_structured("extract entity", SampleSchema)

    assert isinstance(result, SampleSchema)
    assert result.name == "ACME Foods Pvt Ltd"
    assert result.items == ["gstin", "cin"]
    # request went through with_options carrying the timeout + retry config
    assert provider._client.with_options_calls
    opts = provider._client.with_options_calls[0]
    assert opts["timeout"] == provider.timeout
    assert opts["max_retries"] == provider.max_retries
    # a json_schema structured-output constraint was sent
    sent = provider._client.messages.calls[0]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    # usage tracking incremented
    assert llm_calls_var.get() == 1
    assert token_usage_var.get() == 13 + 9


@pytest.mark.anyio
async def test_malformed_json_is_rejected():
    provider = _provider(_Resp("this is not json"))
    with pytest.raises(LLMProviderException, match="schema validation"):
        await provider.generate_structured("p", SampleSchema)
    # a failed call must not consume call budget
    assert llm_calls_var.get() == 0


@pytest.mark.anyio
async def test_schema_violating_json_is_rejected():
    # missing the required "name" field
    provider = _provider(_Resp(json.dumps({"items": ["x"]})))
    with pytest.raises(LLMProviderException, match="schema validation"):
        await provider.generate_structured("p", SampleSchema)


@pytest.mark.anyio
async def test_response_without_text_block_is_rejected():
    provider = _provider(_Resp(blocks=[]))
    with pytest.raises(LLMProviderException, match="no structured text"):
        await provider.generate_structured("p", SampleSchema)


@pytest.mark.anyio
async def test_refusal_stop_reason_is_rejected():
    provider = _provider(_Resp(json.dumps({"name": "x"}), stop_reason="refusal"))
    with pytest.raises(LLMProviderException, match="refused"):
        await provider.generate_structured("p", SampleSchema)


# --------------------------------------------------------------------------- #
# 3. Timeout & graceful failure handling
# --------------------------------------------------------------------------- #
@pytest.mark.anyio
async def test_provider_api_error_is_wrapped_not_leaked():
    def _raise(**_kwargs):
        raise RuntimeError("socket hang up to api.anthropic.com")

    provider = _provider(_raise)
    with pytest.raises(LLMProviderException) as excinfo:
        await provider.generate_structured("p", SampleSchema)
    # only the exception class name is surfaced (no raw provider/request detail)
    assert "Anthropic provider call failed: RuntimeError" in str(excinfo.value)
    assert "socket hang up" not in str(excinfo.value)


@pytest.mark.anyio
async def test_timeout_is_mapped_to_llm_provider_exception(monkeypatch):
    async def _timeout(aw=None, *_a, **_k):
        # close the (unawaited) inner coroutine to avoid a RuntimeWarning
        if aw is not None and hasattr(aw, "close"):
            aw.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(llm_mod.asyncio, "wait_for", _timeout)
    provider = _provider(_Resp(json.dumps({"name": "x"})))
    with pytest.raises(LLMProviderException, match="timed out"):
        await provider.generate_structured("p", SampleSchema)


@pytest.mark.anyio
async def test_missing_sdk_or_api_key_raises_provider_exception(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    provider = AnthropicLLMProvider(provider="anthropic")  # no injected client
    with pytest.raises(
        LLMProviderException,
        match=r"(anthropic' SDK is not installed|API keys and configuration for production provider)",
    ):
        await provider.generate_structured("p", SampleSchema)


@pytest.mark.anyio
async def test_call_budget_is_enforced_before_calling_provider():
    settings = get_settings()
    old = settings.max_llm_calls
    try:
        settings.max_llm_calls = 1
        llm_calls_var.set(1)
        provider = _provider(_Resp(json.dumps({"name": "x"})))
        with pytest.raises(LLMProviderException, match="Max LLM calls limit reached"):
            await provider.generate_structured("p", SampleSchema)
        # provider was never actually invoked
        assert provider._client.messages.calls == []
    finally:
        settings.max_llm_calls = old


def test_run_structured_sync_is_inert_for_mock_provider():
    assert run_structured_sync(MockLLMProvider(), "p", SampleSchema) is None
    assert run_structured_sync(None, "p", SampleSchema) is None


def test_run_structured_sync_swallows_failures_and_returns_none():
    def _raise(**_kwargs):
        raise RuntimeError("boom")

    assert run_structured_sync(_provider(_raise), "p", SampleSchema) is None


def test_run_structured_sync_happy_path_returns_validated_model():
    out = run_structured_sync(
        _provider(_Resp(json.dumps({"name": "ACME", "items": []}))),
        "p",
        SampleSchema,
    )
    assert isinstance(out, SampleSchema)
    assert out.name == "ACME"


# --------------------------------------------------------------------------- #
# 4. The LLM can NEVER determine the final numerical risk score / level
# --------------------------------------------------------------------------- #
def test_risk_engine_has_no_llm_seam():
    # The deterministic engine takes only evidence + investigation id.
    assert set(inspect.signature(calculate_risk_analysis).parameters) == {
        "evidences_raw",
        "investigation_id",
    }
    assert set(inspect.signature(run_all_rules).parameters) == {"evidences"}


def test_llm_narrative_schemas_expose_no_score_fields():
    for schema in (ReportNarrative, QAReasoning):
        fields = set(schema.model_fields)
        for banned in ("score", "risk_score", "overall_risk", "risk_level", "level"):
            assert banned not in fields


@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _seed(db):
    inv = Investigation(input_data='{"business_name": "Test Company"}')
    db.add(inv)
    db.commit()
    db.refresh(inv)

    cand = ResearchResult(
        result_id="C1",
        task_id="T-DISC",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "confidence": 0.95, "match_type": "EXACT"}],
        source_name="discovery_agent",
        source_url=None,
        retrieved_at="2026-08-26T10:00:00+00:00",
        confidence=0.95,
    )
    gst = ResearchResult(
        result_id="R1",
        task_id="T-GST",
        field_name="gst_status",
        field_value="Inactive",
        source_name="GST Portal",
        source_url="https://www.gst.gov.in",
        retrieved_at="2026-08-26T10:00:00+00:00",
        confidence=0.95,
    )
    save_research_result(db, cand, inv.id)
    save_research_result(db, gst, inv.id)
    return inv.id


def test_report_score_comes_only_from_engine_even_with_a_live_llm(db_session):
    inv_id = _seed(db_session)

    # An LLM that actively tries to inject its own (wrong) score via the narrative.
    narrative_json = json.dumps(
        {
            "narrative_summary": "In my opinion the risk score is 999/100 and the level is CRITICAL.",
            "cross_source_consistency_summary": "n/a",
            "recommended_verification_focus": ["gst"],
        }
    )
    fake_llm = _provider(_Resp(narrative_json))

    report = generate_investigation_report(db_session, inv_id, llm=fake_llm)

    # recompute independently from persisted evidence
    engine = calculate_risk_analysis(get_evidences_for_investigation(db_session, inv_id))

    # The narrative was generated by the (fake) real provider ...
    assert "999" in report["narrative"]
    # ... but the authoritative score/level are the engine's, not the LLM's.
    assert report["overall_risk"]["score"] == engine["overall_risk"]["score"]
    assert report["overall_risk"]["level"] == engine["overall_risk"]["level"]
    assert report["overall_risk"]["score"] == 30  # GST_INACTIVE weight
    assert report["overall_risk"]["score"] != 999


def test_qa_status_stays_deterministic_despite_llm_advisory(db_session):
    inv_id = _seed(db_session)
    # produce a report first (mock path is fine for this step)
    generate_investigation_report(db_session, inv_id)

    qa_json = json.dumps(
        {
            "overall_assessment": "FAIL",
            "advisory_notes": ["I think this report should FAIL and the score is wrong."],
        }
    )
    fake_llm = _provider(_Resp(qa_json))

    result = validate_report(db_session, inv_id, llm=fake_llm)

    # Deterministic QA remains the sole authority for PASS/FAIL and score checks.
    engine_score = analyze_investigation(db_session, inv_id)["overall_risk"]["score"]
    assert result["status"] == "PASS"
    assert result["score_verified"] is True
    assert engine_score == 30
    # the LLM's opinion is retained only as an advisory note
    assert result["advisory_notes"] == [
        "I think this report should FAIL and the score is wrong."
    ]
