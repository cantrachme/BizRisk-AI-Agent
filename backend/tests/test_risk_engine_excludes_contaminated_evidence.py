"""
Risk Engine: defense-in-depth exclusion of contaminated evidence.

Production persistence (app/validation/research.py) already filters out
records whose verification_status is REJECTED / ENTITY_MISMATCH / UNRELATED
before they reach the database. This is a separate, independent gate inside
`calculate_risk_analysis` itself: even if it is called directly with raw
evidence (bypassing persistence), or upstream filtering ever regresses, such
records must never participate in risk scoring.

This does NOT change persistence filtering, browser/entity-resolution
behaviour, or any rule's weight/semantics. Excluded evidence is dropped
entirely -- it never becomes a risk signal, and it is never treated as
negative evidence either (it simply contributes nothing, same as if it had
never been collected).

No company-specific values are used.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest

from app.graph.state import ResearchResult
from app.risk.engine import EXCLUDED_VERIFICATION_STATUSES, calculate_risk_analysis


def _r(rid, fn, fv, src, conf=0.9, vs="VERIFIED", at="2026-09-04T00:00:00Z"):
    return ResearchResult(
        result_id=rid, task_id="T", field_name=fn, field_value=fv, source_name=src,
        source_url=None, retrieved_at=at, confidence=conf, verification_status=vs,
    )


def test_excluded_statuses_are_exactly_the_three_requested():
    assert EXCLUDED_VERIFICATION_STATUSES == {"REJECTED", "ENTITY_MISMATCH", "UNRELATED"}


# --------------------------------------------------------------------------- #
# 1. each excluded status cannot create a risk signal, on its own
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["REJECTED", "ENTITY_MISMATCH", "UNRELATED"])
def test_excluded_status_adverse_looking_record_produces_no_signal(status):
    out = calculate_risk_analysis([
        _r("R1", "company_status", "STRUCK OFF", "MCA Portal", conf=0.95, vs=status),
    ])
    assert out["risk_signals"] == [], status
    # No other evidence -> nothing substantive remains -> insufficient, not a
    # clean 0/LOW score manufactured from a discarded record.
    assert out["insufficient_evidence"] is True, status
    assert out["overall_risk"]["score"] is None, status
    assert out["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE", status


@pytest.mark.parametrize("status", ["REJECTED", "ENTITY_MISMATCH", "UNRELATED"])
def test_excluded_status_gst_inactive_looking_record_produces_no_signal(status):
    out = calculate_risk_analysis([
        _r("R1", "gst_status", "cancelled", "GST Portal", conf=0.95, vs=status),
    ])
    assert out["risk_signals"] == [], status
    assert out["insufficient_evidence"] is True, status


# --------------------------------------------------------------------------- #
# 2. the same factual evidence with an allowed status still produces the
#    signal -- the exclusion is status-specific, not field/value-specific
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["VERIFIED", "UNVERIFIED"])
def test_same_evidence_with_allowed_status_still_triggers_signal(status):
    out = calculate_risk_analysis([
        _r("R1", "company_status", "STRUCK OFF", "MCA Portal", conf=0.9, vs=status),
    ])
    codes_weights = [(s["code"], s["risk_weight"]) for s in out["risk_signals"]]
    assert codes_weights == [("COMPANY_STATUS_ADVERSE", 35)], status
    assert out["insufficient_evidence"] is False, status
    assert out["overall_risk"]["score"] == 35, status


def test_excluded_record_alongside_valid_evidence_is_simply_dropped():
    # A REJECTED record must not affect scoring at all -- it neither creates a
    # signal nor blocks the genuine, allowed-status evidence from scoring.
    out = calculate_risk_analysis([
        _r("R1", "company_status", "STRUCK OFF", "MCA Portal", conf=0.95, vs="REJECTED"),
        _r("R2", "gst_status", "cancelled", "GST Portal", conf=0.9, vs="VERIFIED"),
    ])
    codes_weights = [(s["code"], s["risk_weight"]) for s in out["risk_signals"]]
    assert codes_weights == [("GST_INACTIVE", 30)]
    assert out["overall_risk"]["score"] == 30
    assert out["insufficient_evidence"] is False


# --------------------------------------------------------------------------- #
# 3. excluded evidence is not treated as negative -- its absence/exclusion
#    must score identically to it never having existed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["REJECTED", "ENTITY_MISMATCH", "UNRELATED"])
def test_excluded_record_scores_identically_to_absent_record(status):
    with_excluded = calculate_risk_analysis([
        _r("R1", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "MCA Portal", conf=0.9),
        _r("R2", "company_status", "DISSOLVED", "Some Scraper", conf=0.95, vs=status),
    ])
    without = calculate_risk_analysis([
        _r("R1", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "MCA Portal", conf=0.9),
    ])
    assert with_excluded["overall_risk"] == without["overall_risk"], status
    assert with_excluded["risk_signals"] == without["risk_signals"] == []
    assert with_excluded["insufficient_evidence"] == without["insufficient_evidence"] is False


# --------------------------------------------------------------------------- #
# Regression: an excluded adverse-looking record cannot bypass the substantive
# sufficiency gate ([[test_evidence_sufficiency_substantive]]-style check)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["REJECTED", "ENTITY_MISMATCH", "UNRELATED"])
def test_excluded_record_cannot_satisfy_substantive_sufficiency_gate(status):
    # A weak availability field cannot establish sufficiency on its own
    # (existing behaviour); pairing it with an excluded-status adverse-looking
    # substantive-field record must not "unlock" sufficiency either.
    out = calculate_risk_analysis([
        _r("R1", "epfo_status", "AVAILABLE", "EPFO Portal", conf=0.9),
        _r("R2", "company_status", "STRUCK OFF", "Some Scraper", conf=0.99, vs=status),
    ])
    assert out["insufficient_evidence"] is True, status
    assert out["overall_risk"]["score"] is None, status
    assert out["risk_signals"] == [], status


# --------------------------------------------------------------------------- #
# Existing insufficient-evidence / VERIFIED / unverified behaviour preserved
# --------------------------------------------------------------------------- #
def test_verified_and_unverified_allowed_statuses_unaffected():
    verified = calculate_risk_analysis([
        _r("R1", "gst_status", "active", "GST Portal", conf=0.6, vs="VERIFIED"),
    ])
    assert verified["insufficient_evidence"] is False
    assert verified["overall_risk"]["score"] == 0

    unverified_low_conf = calculate_risk_analysis([
        _r("R1", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "General Web", conf=0.6, vs="UNVERIFIED"),
    ])
    assert unverified_low_conf["insufficient_evidence"] is True

    unverified_high_conf = calculate_risk_analysis([
        _r("R1", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "MCA Portal", conf=0.9, vs="UNVERIFIED"),
    ])
    assert unverified_high_conf["insufficient_evidence"] is False
