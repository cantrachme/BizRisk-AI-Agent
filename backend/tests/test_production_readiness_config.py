"""
Focused tests for the production-readiness configuration gaps closed in the
final audit:

  * entity-resolution acceptance threshold is configurable (was hardcoded 0.75)
  * QA -> planner correction-loop cap is configurable (was hardcoded 2)
  * the unauthenticated /api/v1/test/* endpoints are gated by a setting and
    forbidden in a production environment
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.entity_resolution.resolver import resolve_entity
from app.graph.workflow import should_continue_after_qa
from app.main import app

client = TestClient(app)


# --------------------------------------------------------------------------- #
# Settings validation
# --------------------------------------------------------------------------- #
def test_new_settings_have_expected_defaults():
    s = Settings()
    assert s.entity_resolution_threshold == 0.75
    assert s.max_qa_loops == 2
    assert s.enable_test_endpoints is True


@pytest.mark.parametrize("bad", [-0.1, 1.5, 2.0])
def test_entity_resolution_threshold_bounds_enforced(bad):
    with pytest.raises(ValueError, match="entity_resolution_threshold must be between"):
        Settings(entity_resolution_threshold=bad)


def test_max_qa_loops_must_be_non_negative():
    with pytest.raises(ValueError, match="Limit must be non-negative"):
        Settings(max_qa_loops=-1)


def test_production_forbids_unauthenticated_test_endpoints():
    with pytest.raises(ValueError, match="enable_test_endpoints must be False in production"):
        Settings(environment="production", debug=False, enable_test_endpoints=True)
    # a fully prod-safe config constructs cleanly
    Settings(environment="production", debug=False, enable_test_endpoints=False)


# --------------------------------------------------------------------------- #
# Entity-resolution threshold is actually consulted at runtime
# --------------------------------------------------------------------------- #
def _partial_match_case():
    # name-only similarity resolves to score 0.8 (2/3 token f1 on the candidate)
    target = {"name": "ACME FOODS PRIVATE LIMITED"}
    candidates = [{"name": "ACME FOODS AND BEVERAGES PRIVATE LIMITED"}]
    return target, candidates


def test_resolver_respects_configured_threshold(monkeypatch):
    target, candidates = _partial_match_case()

    monkeypatch.setattr(get_settings(), "entity_resolution_threshold", 0.75)
    res_low = resolve_entity(target, candidates)
    assert res_low["matched"] is True
    assert res_low["confidence"] == pytest.approx(0.8, abs=1e-6)
    assert "0.75" in " ".join(res_low["match_reasons"])

    monkeypatch.setattr(get_settings(), "entity_resolution_threshold", 0.85)
    res_high = resolve_entity(target, candidates)
    assert res_high["matched"] is False
    assert res_high["resolution_status"] == "ENTITY_UNRESOLVED"
    assert res_high["confidence"] == pytest.approx(0.8, abs=1e-6)  # score unchanged, verdict changed


# --------------------------------------------------------------------------- #
# QA correction-loop cap is configurable
# --------------------------------------------------------------------------- #
def _failing_qa_state(loop_count: int) -> dict:
    return {
        "qa_result": {"status": "FAIL", "issues": [{"type": "MISSING_EVIDENCE"}]},
        "qa_loop_count": loop_count,
    }


def test_qa_loop_cap_is_configurable(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_qa_loops", 2)
    assert should_continue_after_qa(_failing_qa_state(0)) == "planner"
    assert should_continue_after_qa(_failing_qa_state(1)) == "planner"
    assert should_continue_after_qa(_failing_qa_state(2)) == "__end__"

    monkeypatch.setattr(get_settings(), "max_qa_loops", 1)
    assert should_continue_after_qa(_failing_qa_state(0)) == "planner"
    assert should_continue_after_qa(_failing_qa_state(1)) == "__end__"

    monkeypatch.setattr(get_settings(), "max_qa_loops", 4)
    assert should_continue_after_qa(_failing_qa_state(3)) == "planner"


# --------------------------------------------------------------------------- #
# /api/v1/test/* endpoints are gated by the setting
# --------------------------------------------------------------------------- #
def test_test_endpoints_available_when_enabled():
    assert get_settings().enable_test_endpoints is True
    r = client.post("/api/v1/test/intake", json={"business_name": "Sample Co"})
    assert r.status_code == 200
    assert r.json()["business_name"] == "SAMPLE CO"


def test_test_endpoints_return_404_when_disabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "enable_test_endpoints", False)
    r = client.post("/api/v1/test/intake", json={"business_name": "Sample Co"})
    assert r.status_code == 404
    # a real (non-test) route is unaffected
    assert client.get("/health").status_code == 200
