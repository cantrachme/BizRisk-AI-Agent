"""
Regression for the narrow insufficient-evidence consistency fix.

A numeric (LOW) risk score must not be produced when no *verified* factual
evidence exists. "Verified" == confidence >= VERIFIED_EVIDENCE_CONFIDENCE (0.70)
OR verification_status explicitly "VERIFIED". candidate_entities never counts.
When verified evidence does exist, scoring/rule behaviour is unchanged.

No company-specific values are used.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from app.graph.state import ResearchResult
from app.risk.engine import calculate_risk_analysis, VERIFIED_EVIDENCE_CONFIDENCE


def _rr(result_id, field_name, field_value, confidence, source_name="Some Source",
        verification_status="UNVERIFIED"):
    return ResearchResult(
        result_id=result_id,
        task_id="TASK-1",
        field_name=field_name,
        field_value=field_value,
        source_name=source_name,
        source_url="https://example.test/x",
        retrieved_at="2026-01-01T00:00:00+00:00",
        confidence=confidence,
        verification_status=verification_status,
    )


def _codes(analysis):
    return sorted(s["code"] for s in analysis["risk_signals"])


# 1. Only factual evidence with confidence 0.50-0.69 => INSUFFICIENT_EVIDENCE
def test_only_low_confidence_factual_evidence_is_insufficient():
    for conf in (0.50, 0.55, 0.60, 0.69):
        analysis = calculate_risk_analysis([
            _rr("R1", "business_activity", "retail trade of textiles", conf),
            _rr("R2", "registered_address", "12 Long Road, Sector 5, Example City", conf),
        ])
        assert analysis["insufficient_evidence"] is True, conf
        assert analysis["overall_risk"]["score"] is None, conf
        assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE", conf
        assert analysis["risk_signals"] == [], conf


# 2. candidate_entities only => INSUFFICIENT_EVIDENCE (even at high confidence)
def test_candidate_entities_only_is_insufficient():
    analysis = calculate_risk_analysis([
        _rr("R1", "candidate_entities",
            [{"name": "EXAMPLE ENTERPRISES PRIVATE LIMITED", "confidence": 0.95}],
            0.95, source_name="discovery_agent", verification_status="VERIFIED"),
    ])
    assert analysis["insufficient_evidence"] is True
    assert analysis["overall_risk"]["score"] is None
    assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"


# 3. Verified evidence (>= 0.70) => normal numeric risk calculation
def test_high_confidence_evidence_produces_numeric_score():
    analysis = calculate_risk_analysis([
        _rr("R1", "legal_name", "EXAMPLE ENTERPRISES PRIVATE LIMITED", 0.92,
            source_name="GST Portal"),
        _rr("R2", "gst_status", "active", 0.92, source_name="GST Portal"),
    ])
    assert analysis["insufficient_evidence"] is False
    assert analysis["overall_risk"]["score"] == 0
    assert analysis["overall_risk"]["level"] == "LOW"
    assert analysis["risk_signals"] == []

    # and an adverse verified record still scores normally
    adverse = calculate_risk_analysis([
        _rr("R1", "gst_status", "inactive", 0.90, source_name="GST Portal"),
        _rr("R2", "legal_name", "EXAMPLE ENTERPRISES PRIVATE LIMITED", 0.90,
            source_name="GST Portal"),
    ])
    assert adverse["insufficient_evidence"] is False
    assert adverse["overall_risk"]["score"] == 30
    assert _codes(adverse) == ["GST_INACTIVE"]


# 4. Explicitly VERIFIED evidence below 0.70 => still sufficient
def test_explicit_verified_status_below_threshold_is_sufficient():
    assert VERIFIED_EVIDENCE_CONFIDENCE == 0.70
    analysis = calculate_risk_analysis([
        _rr("R1", "gst_status", "active", 0.60, source_name="GST Portal",
            verification_status="VERIFIED"),
    ])
    assert analysis["insufficient_evidence"] is False
    assert analysis["overall_risk"]["score"] == 0
    assert analysis["overall_risk"]["level"] == "LOW"


# 5. Existing adverse verified evidence still produces the same score/signals
def test_adverse_verified_evidence_scoring_unchanged():
    evs = [
        _rr("R1", "gst_status", "inactive", 0.95, source_name="GST Portal"),
        _rr("R2", "legal_name", "EXAMPLE ENTERPRISES PRIVATE LIMITED", 0.95,
            source_name="GST Portal"),
        _rr("R3", "legal_name", "DIFFERENT HOLDINGS LIMITED", 0.90,
            source_name="MCA Portal"),
    ]
    analysis = calculate_risk_analysis(evs)
    assert analysis["insufficient_evidence"] is False
    # GST_INACTIVE (30) + LEGAL_NAME_CONFLICT (25) = 55, unchanged by the fix
    assert analysis["overall_risk"]["score"] == 55
    assert analysis["overall_risk"]["level"] == "MODERATE"
    assert _codes(analysis) == ["GST_INACTIVE", "LEGAL_NAME_CONFLICT"]

    # low-confidence records still participate in rule evaluation once the
    # investigation is sufficient (behaviour preserved): a 0.60 conflicting name
    # alongside a verified anchor still triggers the conflict rule.
    mixed = calculate_risk_analysis([
        _rr("A", "legal_name", "EXAMPLE ENTERPRISES PRIVATE LIMITED", 0.95,
            source_name="GST Portal"),
        _rr("B", "legal_name", "DIFFERENT HOLDINGS LIMITED", 0.60,
            source_name="General Web"),
    ])
    assert mixed["insufficient_evidence"] is False
    assert _codes(mixed) == ["LEGAL_NAME_CONFLICT"]
    assert mixed["overall_risk"]["score"] == 25


# 6. Rejected / unavailable evidence does not make the investigation sufficient
def test_rejected_and_unavailable_evidence_stays_insufficient():
    analysis = calculate_risk_analysis([
        _rr("R1", "gst_status", "SOURCE_UNAVAILABLE", 0.95, source_name="GST Portal",
            verification_status="SOURCE_UNAVAILABLE"),
        _rr("R2", "mca_status", "NOT_FOUND", 0.95, source_name="MCA Portal",
            verification_status="NOT_FOUND"),
        _rr("R3", "registered_address", "NOT_FOUND", 0.95, source_name="MCA Portal"),
        _rr("R4", "legal_name", "UNAVAILABLE", 0.95, source_name="GST Portal"),
    ])
    assert analysis["insufficient_evidence"] is True
    assert analysis["overall_risk"]["score"] is None
    assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"
