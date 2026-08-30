import pytest
from app.risk.engine import calculate_risk_analysis
from app.graph.state import ResearchResult
from datetime import datetime, timezone


import uuid

def make_evidence(field_name, field_value, source_name="Test Source", confidence=1.0):
    return ResearchResult(
        result_id=f"EV-{field_name}-{uuid.uuid4().hex[:6]}",
        task_id="TASK-001",
        field_name=field_name,
        field_value=field_value,
        source_name=source_name,
        source_url="http://test.com",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=confidence,
    )


# 1. Test score contribution for single rules
def test_risk_engine_score_contributions():
    # Only VERY_RECENT_REGISTRATION (weight: 5)
    recent_reg_dt = datetime.now(timezone.utc).isoformat()
    evs = [
        make_evidence("incorporation_date", recent_reg_dt),
        make_evidence("candidate_entities", [{"business_name": "ABC", "name": "ABC"}])
    ]
    res = calculate_risk_analysis(evs)
    assert res["overall_risk"]["score"] == 5
    assert res["overall_risk"]["level"] == "LOW"

    # Only GST_INACTIVE (weight: 30)
    evs2 = [
        make_evidence("gst_status", "inactive")
    ]
    res2 = calculate_risk_analysis(evs2)
    assert res2["overall_risk"]["score"] == 30
    assert res2["overall_risk"]["level"] == "LOW"


# 2. Test category weight aggregations
def test_risk_engine_category_weights():
    # Triggering both address mismatch and business activity mismatch (both CONSISTENCY category, weight: 10 each)
    evs = [
        make_evidence("address", "123 Main St, Delhi", source_name="GST"),
        make_evidence("address", "456 Side St, Mumbai", source_name="MCA"),
        make_evidence("business_activity", "Agriculture", source_name="GST"),
        make_evidence("business_activity", "Software", source_name="MCA"),
    ]
    res = calculate_risk_analysis(evs)
    # Check consistency category score
    assert res["category_scores"]["consistency"] == 20
    # Overall score = 10 (address) + 10 (activity) = 20
    assert res["overall_risk"]["score"] == 20


# 3. Test risk level boundary conditions
@pytest.mark.parametrize(
    "active_rules, expected_score, expected_level",
    [
        # Boundary low -> moderate (30 vs 31)
        # GST_INACTIVE (30) -> Score 30 -> LOW
        (["GST_INACTIVE"], 30, "LOW"),
        # GST_INACTIVE (30) + VERY_RECENT_REGISTRATION (5) -> Score 35 -> MODERATE
        (["GST_INACTIVE", "VERY_RECENT_REGISTRATION"], 35, "MODERATE"),
        
        # Boundary moderate -> high (60 vs 61)
        # GST_INACTIVE (30) + LEGAL_NAME_CONFLICT (25) + VERY_RECENT_REGISTRATION (5) -> Score 60 -> MODERATE
        (["GST_INACTIVE", "LEGAL_NAME_CONFLICT", "VERY_RECENT_REGISTRATION"], 60, "MODERATE"),
        # GST_INACTIVE (30) + LEGAL_NAME_CONFLICT (25) + ADDRESS_MAJOR_MISMATCH (10) -> Score 65 -> HIGH
        (["GST_INACTIVE", "LEGAL_NAME_CONFLICT", "ADDRESS_MAJOR_MISMATCH"], 65, "HIGH"),
        
        # Boundary high -> very_high (80 vs 81)
        # GST_INACTIVE (30) + LEGAL_NAME_CONFLICT (25) + ADDRESS_MAJOR_MISMATCH (10) + BUSINESS_ACTIVITY_MISMATCH (10) + VERY_RECENT_REGISTRATION (5) -> Score 80 -> HIGH
        (["GST_INACTIVE", "LEGAL_NAME_CONFLICT", "ADDRESS_MAJOR_MISMATCH", "BUSINESS_ACTIVITY_MISMATCH", "VERY_RECENT_REGISTRATION"], 80, "HIGH"),
    ]
)
def test_risk_engine_level_boundaries(active_rules, expected_score, expected_level):
    evs = []
    # Build evidences according to active_rules
    if "GST_INACTIVE" in active_rules:
        evs.append(make_evidence("gst_status", "inactive"))
    if "LEGAL_NAME_CONFLICT" in active_rules:
        evs.append(make_evidence("legal_name", "Alpha Pvt Ltd", source_name="GST"))
        evs.append(make_evidence("legal_name", "Beta Pvt Ltd", source_name="MCA"))
    if "ADDRESS_MAJOR_MISMATCH" in active_rules:
        evs.append(make_evidence("address", "123 Delhi St", source_name="GST"))
        evs.append(make_evidence("address", "456 Mumbai St", source_name="MCA"))
    if "BUSINESS_ACTIVITY_MISMATCH" in active_rules:
        evs.append(make_evidence("business_activity", "Tech", source_name="GST"))
        evs.append(make_evidence("business_activity", "Finance", source_name="MCA"))
    if "VERY_RECENT_REGISTRATION" in active_rules:
        recent_reg_dt = datetime.now(timezone.utc).isoformat()
        evs.append(make_evidence("incorporation_date", recent_reg_dt))
        evs.append(make_evidence("candidate_entities", [{"business_name": "ABC", "name": "ABC"}]))

    res = calculate_risk_analysis(evs)
    assert res["overall_risk"]["score"] == expected_score
    assert res["overall_risk"]["level"] == expected_level
