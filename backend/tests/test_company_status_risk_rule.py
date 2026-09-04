"""
Risk Engine: adverse company/registration status rule (COMPANY_STATUS_ADVERSE).

Fixes the confirmed HIGH-severity gap where `company_status` was never
evaluated by any rule, so a company reported as STRUCK OFF / UNDER LIQUIDATION /
DORMANT / DISSOLVED scored identically to an ACTIVE one.

Generic and deterministic: matches a closed set of canonical, UNAMBIGUOUS
company-level adverse registry status values, never a company name.

"cancelled" / "suspended" / "inactive" are deliberately EXCLUDED from this
rule's vocabulary: the company-status extractor scans for those same words
using GST-style status wording, so a "company_status" value of
cancelled/suspended/inactive is not reliably a company-level registry finding.
Those words remain evaluate_gst_inactive's exclusive concern for `gst_status`.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.graph.state import ResearchResult
from app.models.investigation import Investigation
from app.models.risk_signal import RiskSignal
from app.risk.engine import calculate_risk_analysis
from app.risk.rules import evaluate_company_status_adverse, normalize_evidence
from app.services.evidence import save_research_results
from app.services.risk_analysis import analyze_investigation


def _r(rid, fn, fv, src, conf=0.9, vs="VERIFIED", at="2026-09-05T00:00:00Z"):
    return ResearchResult(
        result_id=rid, task_id="T", field_name=fn, field_value=fv, source_name=src,
        source_url=None, retrieved_at=at, confidence=conf, verification_status=vs,
    )


# --------------------------------------------------------------------------- #
# ACTIVE must never trigger
# --------------------------------------------------------------------------- #
def test_active_status_triggers_no_signal():
    out = calculate_risk_analysis([_r("R1", "company_status", "ACTIVE", "MCA Portal")])
    assert out["risk_signals"] == []
    assert out["overall_risk"]["score"] == 0
    assert out["overall_risk"]["level"] == "LOW"


@pytest.mark.parametrize("value", ["active", "Active", "ACTIVE", " active "])
def test_active_case_whitespace_variants_never_trigger(value):
    out = calculate_risk_analysis([_r("R1", "company_status", value, "MCA Portal")])
    assert not any(s["code"] == "COMPANY_STATUS_ADVERSE" for s in out["risk_signals"])


# --------------------------------------------------------------------------- #
# Each adverse status produces a positive risk signal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [
    "STRUCK OFF", "Strike Off", "Strikeoff", "under liquidation", "In Liquidation", "Liquidated",
    "Dissolved", "Deregistered", "De-Registered", "Wound Up", "Winding Up", "Defunct", "DORMANT",
])
def test_adverse_company_status_triggers_signal(value):
    out = calculate_risk_analysis([_r("R1", "company_status", value, "MCA Portal")])
    codes = [s["code"] for s in out["risk_signals"]]
    assert "COMPANY_STATUS_ADVERSE" in codes
    assert out["overall_risk"]["score"] > 0


def test_struck_off_positive_signal_with_expected_weight_category_severity():
    out = calculate_risk_analysis([_r("R1", "company_status", "STRUCK OFF", "MCA Portal", conf=0.9)])
    sig = next(s for s in out["risk_signals"] if s["code"] == "COMPANY_STATUS_ADVERSE")
    assert sig["risk_weight"] == 35
    assert sig["category"] == "COMPLIANCE"
    assert sig["severity"] == "HIGH"
    assert sig["confidence"] == 0.9
    assert out["overall_risk"]["score"] == 35
    assert out["overall_risk"]["level"] == "MODERATE"
    assert out["category_scores"]["compliance"] == 35


def test_under_liquidation_positive_signal():
    out = calculate_risk_analysis([_r("R1", "company_status", "Under Liquidation", "Zauba Corp", conf=0.75)])
    assert any(s["code"] == "COMPANY_STATUS_ADVERSE" for s in out["risk_signals"])
    assert out["overall_risk"]["score"] == 35


def test_dormant_positive_signal():
    out = calculate_risk_analysis([_r("R1", "company_status", "Dormant", "Tofler", conf=0.75)])
    assert any(s["code"] == "COMPANY_STATUS_ADVERSE" for s in out["risk_signals"])
    assert out["overall_risk"]["score"] == 35


# --------------------------------------------------------------------------- #
# Case / whitespace normalization
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [
    "struck off", "STRUCK OFF", "Struck Off", "  struck   off  ", "struck\toff",
])
def test_case_and_whitespace_variations_normalize_correctly(value):
    out = calculate_risk_analysis([_r("R1", "company_status", value, "MCA Portal")])
    assert any(s["code"] == "COMPANY_STATUS_ADVERSE" for s in out["risk_signals"]), value


def test_rule_function_directly_normalizes_case_and_whitespace():
    evs = [normalize_evidence(_r("R1", "company_status", "  Under   Liquidation  ", "MCA Portal"))]
    res = evaluate_company_status_adverse(evs)
    assert res is not None
    assert res["triggered"] is True
    assert res["evidence_ids"] == ["R1"]


# --------------------------------------------------------------------------- #
# Unrelated / unknown status values never trigger (no fabrication)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [
    "Pending Approval", "Allocated", "Registered", "Amalgamated", "Not Available", "In Process",
])
def test_unrelated_or_unknown_status_does_not_trigger(value):
    out = calculate_risk_analysis([_r("R1", "company_status", value, "MCA Portal")])
    assert not any(s["code"] == "COMPANY_STATUS_ADVERSE" for s in out["risk_signals"])


# --------------------------------------------------------------------------- #
# GST-style words ("cancelled" / "suspended" / "inactive") must NOT be treated
# as company-level adverse status -- they are ambiguous under company_status
# (the extractor borrows GST-style wording) and must never be inferred as
# company-level risk.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["Cancelled", "CANCELLED", "cancelled", "Suspended", "SUSPENDED", "Inactive", "INACTIVE"])
def test_gst_style_words_do_not_trigger_company_status_risk_on_company_status(value):
    out = calculate_risk_analysis([_r("R1", "company_status", value, "MCA Portal")])
    assert not any(s["code"] == "COMPANY_STATUS_ADVERSE" for s in out["risk_signals"]), value
    assert out["overall_risk"]["score"] == 0


@pytest.mark.parametrize("value", ["Cancelled", "Suspended", "Inactive"])
def test_gst_style_words_do_not_trigger_company_status_risk_on_registration_status(value):
    out = calculate_risk_analysis([_r("R1", "registration_status", value, "MCA Portal")])
    assert not any(s["code"] == "COMPANY_STATUS_ADVERSE" for s in out["risk_signals"]), value


def test_rule_function_directly_rejects_gst_style_words():
    for value in ("cancelled", "canceled", "suspended", "inactive"):
        evs = [normalize_evidence(_r("R1", "company_status", value, "MCA Portal"))]
        assert evaluate_company_status_adverse(evs) is None, value


# --------------------------------------------------------------------------- #
# Existing rules remain unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["inactive", "cancelled", "suspended", "no", "invalid", "false"])
def test_existing_gst_inactive_rule_unaffected(value):
    out = calculate_risk_analysis([_r("R1", "gst_status", value, "GST Portal")])
    codes_weights = [(s["code"], s["risk_weight"]) for s in out["risk_signals"]]
    assert codes_weights == [("GST_INACTIVE", 30)]
    assert out["overall_risk"]["score"] == 30


def test_existing_legal_name_conflict_rule_unaffected():
    out = calculate_risk_analysis([
        _r("R1", "legal_name", "ACME FOODS PRIVATE LIMITED", "GST Portal"),
        _r("R2", "legal_name", "GLOBEX HOLDINGS LIMITED", "Tofler"),
    ])
    codes_weights = [(s["code"], s["risk_weight"]) for s in out["risk_signals"]]
    assert codes_weights == [("LEGAL_NAME_CONFLICT", 25)]
    assert out["overall_risk"]["score"] == 25


def test_existing_address_and_activity_rules_unaffected():
    out = calculate_risk_analysis([
        _r("R1", "registered_address", "12 MG Road, Sector 5, Bangalore 560001", "GST Portal"),
        _r("R2", "registered_address", "88 Residency Road, Pune 411001", "Tofler"),
        _r("R3", "business_activity", "software development services", "Zauba Corp"),
        _r("R4", "business_activity", "textile manufacturing and export", "Tofler"),
    ])
    codes_weights = sorted((s["code"], s["risk_weight"]) for s in out["risk_signals"])
    assert codes_weights == [("ADDRESS_MAJOR_MISMATCH", 10), ("BUSINESS_ACTIVITY_MISMATCH", 10)]
    assert out["overall_risk"]["score"] == 20


def test_multiple_adverse_signals_aggregate_without_double_counting():
    out = calculate_risk_analysis([
        _r("R1", "company_status", "STRUCK OFF", "MCA Portal"),
        _r("R2", "gst_status", "cancelled", "GST Portal"),
        _r("R3", "legal_name", "ACME FOODS PRIVATE LIMITED", "MCA Portal"),
        _r("R4", "legal_name", "GLOBEX HOLDINGS LIMITED", "Tofler"),
    ])
    codes_weights = sorted((s["code"], s["risk_weight"]) for s in out["risk_signals"])
    assert codes_weights == [
        ("COMPANY_STATUS_ADVERSE", 35), ("GST_INACTIVE", 30), ("LEGAL_NAME_CONFLICT", 25),
    ]
    assert out["overall_risk"]["score"] == 90  # 35 + 30 + 25, capped at 100
    assert out["overall_risk"]["level"] == "VERY_HIGH"
    assert out["category_scores"]["compliance"] == 65  # 35 + 30
    assert out["category_scores"]["identity"] == 25


# --------------------------------------------------------------------------- #
# End-to-end: score/category/level are calculated AND persisted correctly
# --------------------------------------------------------------------------- #
@pytest.fixture(name="db")
def _db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def test_company_status_adverse_persists_to_risk_signals_table(db):
    payload = {"business_name": "Sample Enterprises Private Limited"}
    inv = Investigation(id=uuid.uuid4(), input_data=json.dumps(payload), raw_input=json.dumps(payload),
                        status="IN_PROGRESS")
    db.add(inv)
    db.commit()

    save_research_results(db, [
        _r("R1", "company_status", "Struck Off", "MCA Portal", conf=0.9),
    ], inv.id)

    analysis = analyze_investigation(db, inv.id)
    assert analysis["overall_risk"]["score"] == 35
    assert analysis["overall_risk"]["level"] == "MODERATE"
    assert [s["code"] for s in analysis["risk_signals"]] == ["COMPANY_STATUS_ADVERSE"]

    rows = db.query(RiskSignal).filter(RiskSignal.investigation_id == inv.id).all()
    assert len(rows) == 1
    assert rows[0].code == "COMPANY_STATUS_ADVERSE"
    assert rows[0].risk_weight == 35
    assert rows[0].category == "COMPLIANCE"
    assert rows[0].severity == "HIGH"
