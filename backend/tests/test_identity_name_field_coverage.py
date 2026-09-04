"""
Risk Engine: LEGAL_NAME_CONFLICT identity-name field coverage.

Before this fix, evaluate_legal_name_conflict only compared "legal_name" and
"business_name" evidence, so a conflicting "company_name" / "trade_name" /
"establishment_name" value from another source never triggered the signal
even though it describes the same identity concept.

app/agents/browser.py extracts legal_name/company_name/business_name/
establishment_name/trade_name through the exact same regex/cleaning/
validation branch, and app/validation/research.py's LEGAL_NAME_FIELDS applies
the identical is_valid_legal_name check to all five -- the pipeline does not
treat any of them as a legitimately distinct identity (e.g. a separate DBA/
brand name) from legal_name. This fix widens the existing rule's field scope
to that same five-field set; the signal, weight (25), category (IDENTITY),
severity (HIGH), and normalize_name comparison logic are unchanged.

Generic: no company-specific hardcoding, no other rule touched.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest

from app.graph.state import ResearchResult
from app.risk.engine import calculate_risk_analysis
from app.risk.rules import _IDENTITY_NAME_FIELDS, evaluate_legal_name_conflict, normalize_evidence


def _r(rid, fn, fv, src, conf=0.9, vs="VERIFIED", at="2026-09-04T00:00:00Z"):
    return ResearchResult(
        result_id=rid, task_id="T", field_name=fn, field_value=fv, source_name=src,
        source_url=None, retrieved_at=at, confidence=conf, verification_status=vs,
    )


def test_identity_name_field_set_is_exactly_the_five_fields():
    assert _IDENTITY_NAME_FIELDS == {
        "legal_name", "business_name", "company_name", "establishment_name", "trade_name",
    }


# --------------------------------------------------------------------------- #
# 1. existing legal_name / business_name conflict still triggers
# --------------------------------------------------------------------------- #
def test_existing_legal_name_conflict_still_triggers():
    out = calculate_risk_analysis([
        _r("R1", "legal_name", "ACME FOODS PRIVATE LIMITED", "GST Portal"),
        _r("R2", "business_name", "GLOBEX HOLDINGS LIMITED", "MCA Portal"),
    ])
    codes_weights = [(s["code"], s["risk_weight"]) for s in out["risk_signals"]]
    assert codes_weights == [("LEGAL_NAME_CONFLICT", 25)]
    assert out["overall_risk"]["score"] == 25


# --------------------------------------------------------------------------- #
# 2. conflicting values across the newly-covered identity-name fields trigger
#    the same signal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field_name", ["company_name", "trade_name", "establishment_name"])
def test_new_identity_field_conflicting_with_legal_name_triggers(field_name):
    out = calculate_risk_analysis([
        _r("R1", "legal_name", "ACME FOODS PRIVATE LIMITED", "GST Portal"),
        _r("R2", field_name, "GLOBEX HOLDINGS LIMITED", "MCA Portal"),
    ])
    codes_weights = [(s["code"], s["risk_weight"]) for s in out["risk_signals"]]
    assert codes_weights == [("LEGAL_NAME_CONFLICT", 25)], field_name
    assert out["overall_risk"]["score"] == 25
    assert out["overall_risk"]["level"] == "LOW"


def test_conflict_between_two_new_identity_fields_triggers():
    # company_name vs trade_name -- neither is legal_name/business_name, both
    # are new coverage.
    evs = [
        normalize_evidence(_r("R1", "company_name", "ACME FOODS PRIVATE LIMITED", "MCA Portal")),
        normalize_evidence(_r("R2", "trade_name", "GLOBEX HOLDINGS LIMITED", "GST Portal")),
    ]
    res = evaluate_legal_name_conflict(evs)
    assert res is not None
    assert res["triggered"] is True
    assert set(res["evidence_ids"]) == {"R1", "R2"}


# --------------------------------------------------------------------------- #
# 3. matching / normalizable identity-name variants across fields do not
#    trigger a false conflict
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field_name", ["company_name", "trade_name", "establishment_name", "business_name"])
def test_normalizable_identity_variant_across_fields_does_not_trigger(field_name):
    out = calculate_risk_analysis([
        _r("R1", "legal_name", "Acme Foods Private Limited", "GST Portal"),
        _r("R2", field_name, "ACME FOODS PVT LTD", "MCA Portal"),
    ])
    assert not any(s["code"] == "LEGAL_NAME_CONFLICT" for s in out["risk_signals"]), field_name
    assert out["overall_risk"]["score"] == 0


# --------------------------------------------------------------------------- #
# 4. missing optional identity-name fields do not trigger (fewer than 2 name
#    records present at all)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field_name", ["company_name", "trade_name", "establishment_name"])
def test_single_new_identity_field_alone_does_not_trigger(field_name):
    out = calculate_risk_analysis([_r("R1", field_name, "ACME FOODS PRIVATE LIMITED", "MCA Portal")])
    assert not any(s["code"] == "LEGAL_NAME_CONFLICT" for s in out["risk_signals"])


def test_no_identity_name_fields_at_all_does_not_trigger():
    out = calculate_risk_analysis([
        _r("R1", "registered_address", "12 MG Road, Sector 5, Example City 560001", "MCA Portal"),
    ])
    assert not any(s["code"] == "LEGAL_NAME_CONFLICT" for s in out["risk_signals"])


# --------------------------------------------------------------------------- #
# 5. unrelated fields never participate in the name comparison, even when
#    their value looks like a company name
# --------------------------------------------------------------------------- #
def test_business_activity_and_address_never_compared_as_names():
    evs = [
        normalize_evidence(_r("R1", "legal_name", "ACME FOODS PRIVATE LIMITED", "GST Portal")),
        normalize_evidence(_r("R2", "business_activity", "GLOBEX HOLDINGS LIMITED", "MCA Portal")),
        normalize_evidence(_r("R3", "registered_address", "GLOBEX HOLDINGS LIMITED", "MCA Portal")),
    ]
    assert evaluate_legal_name_conflict(evs) is None

    out = calculate_risk_analysis([
        _r("R1", "legal_name", "ACME FOODS PRIVATE LIMITED", "GST Portal"),
        _r("R2", "business_activity", "GLOBEX HOLDINGS LIMITED", "MCA Portal"),
        _r("R3", "registered_address", "GLOBEX HOLDINGS LIMITED, Sector 5", "MCA Portal"),
    ])
    assert not any(s["code"] == "LEGAL_NAME_CONFLICT" for s in out["risk_signals"])


# --------------------------------------------------------------------------- #
# 6. existing clean fixtures remain unchanged
# --------------------------------------------------------------------------- #
def test_existing_address_and_activity_rules_still_unaffected():
    out = calculate_risk_analysis([
        _r("R1", "registered_address", "12 MG Road, Sector 5, Bangalore 560001", "GST Portal"),
        _r("R2", "registered_address", "88 Residency Road, Pune 411001", "Tofler"),
        _r("R3", "business_activity", "software development services", "Zauba Corp"),
        _r("R4", "business_activity", "textile manufacturing and export", "Tofler"),
    ])
    codes_weights = sorted((s["code"], s["risk_weight"]) for s in out["risk_signals"])
    assert codes_weights == [("ADDRESS_MAJOR_MISMATCH", 10), ("BUSINESS_ACTIVITY_MISMATCH", 10)]
    assert out["overall_risk"]["score"] == 20


def test_infosys_shape_style_clean_fixture_unaffected():
    out = calculate_risk_analysis([
        _r("c1", "company_status", "ACTIVE", "QuickCompany", conf=0.8),
        _r("n1", "legal_name", "EXAMPLE ENTERPRISES LIMITED", "Tofler", conf=0.75),
        _r("n2", "legal_name", "Example Enterprises Limited", "Zauba Corp", conf=0.75),
        _r("n3", "company_name", "Example Enterprises Ltd", "General Web", conf=0.6, vs="UNVERIFIED"),
        _r("a1", "registered_address", "12 MG Road, Sector 5, Example City 560100", "Tofler", conf=0.75),
        _r("a2", "registered_address", "12 MG Road, Sector 5, Example City 560100", "Zauba Corp", conf=0.75),
    ])
    assert out["insufficient_evidence"] is False
    assert out["overall_risk"]["score"] == 0
    assert out["overall_risk"]["level"] == "LOW"
    assert out["risk_signals"] == []


def test_multiple_adverse_signals_aggregate_without_double_counting_still_correct():
    out = calculate_risk_analysis([
        _r("R1", "company_status", "STRUCK OFF", "MCA Portal"),
        _r("R2", "gst_status", "cancelled", "GST Portal"),
        _r("R3", "legal_name", "ACME FOODS PRIVATE LIMITED", "MCA Portal"),
        _r("R4", "trade_name", "GLOBEX HOLDINGS LIMITED", "Tofler"),
    ])
    codes_weights = sorted((s["code"], s["risk_weight"]) for s in out["risk_signals"])
    assert codes_weights == [
        ("COMPANY_STATUS_ADVERSE", 35), ("GST_INACTIVE", 30), ("LEGAL_NAME_CONFLICT", 25),
    ]
    assert out["overall_risk"]["score"] == 90
    assert out["overall_risk"]["level"] == "VERY_HIGH"
