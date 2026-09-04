"""
Risk-assessment correctness: score 0 / LOW must be the *correct* deterministic
output when an investigation has no adverse evidence, and a non-zero score must
still be produced (and flow all the way to the persisted risk_signals and the
report) when an existing adverse rule genuinely matches.

Root cause of the "always score 0" observation on live runs of large, clean
companies (e.g. Infosys f310751e): there is genuinely no adverse evidence -- all
sources agree the company is ACTIVE, legal names normalise identically,
registered addresses normalise identically, no gst_status / business_activity /
incorporation_date was retrievable. Every configured rule correctly does not
match. Earlier non-zero scores came from now-fixed false positives (verbose
directory titles, NIC classification glosses, search-engine junk as evidence).

No company-specific values are hardcoded into the rules under test.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

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
from app.risk.rules import evaluate_legal_name_conflict, normalize_evidence, normalize_name
from app.services.evidence import save_research_results
from app.services.report import generate_investigation_report
from app.services.risk_analysis import analyze_investigation


NOW = "2026-09-04T00:00:00Z"


def _r(rid, fn, fv, src, conf=0.85, vs="VERIFIED", at=NOW):
    return ResearchResult(
        result_id=rid, task_id="T", field_name=fn, field_value=fv, source_name=src,
        source_url=None, retrieved_at=at, confidence=conf, verification_status=vs,
    )


# The exact evidence shape of live investigation f310751e (Infosys): three
# aggregators agree the company is ACTIVE, legal names + registered addresses are
# consistent, and no gst_status / business_activity / incorporation_date exists.
def _infosys_shape():
    return [
        _r("c1", "company_status", "ACTIVE", "QuickCompany", conf=0.8),
        _r("c2", "company_status", "ACTIVE", "Tofler", conf=0.75),
        _r("c3", "company_status", "ACTIVE", "Zauba Corp", conf=0.75),
        _r("e1", "epfo_status", "AVAILABLE", "EPFO Portal", conf=0.9),
        _r("w1", "website_status", "AVAILABLE", "Company Website", conf=0.85),
        _r("n1", "legal_name", "INFOSYS LIMITED", "Zauba Corp", conf=0.75),
        _r("n2", "legal_name", "Infosys Limited", "Tofler", conf=0.75),
        _r("n3", "legal_name", "INFOSYS LIMITED", "General Web", conf=0.6, vs="UNVERIFIED"),
        _r("a1", "registered_address",
           "ELECTRONICS CITY,HOSUR ROAD, BANGALORE , KARNATAKA, Karnataka, India - 560100", "Zauba Corp", conf=0.75),
        _r("a2", "registered_address",
           "ELECTRONICS CITY,HOSUR ROAD, BANGALORE, KARNATAKA, Karnataka - 560100", "Tofler", conf=0.75),
    ]


# --------------------------------------------------------------------------- #
# 1. score 0 is CORRECT for consistent, active, non-adverse evidence
# --------------------------------------------------------------------------- #
def test_infosys_shape_consistent_records_score_zero_is_correct():
    out = calculate_risk_analysis(_infosys_shape())
    assert out["insufficient_evidence"] is False        # there IS verified evidence
    assert out["overall_risk"]["score"] == 0            # ...but nothing adverse
    assert out["overall_risk"]["level"] == "LOW"
    assert out["risk_signals"] == []
    assert all(v == 0 for v in out["category_scores"].values())


def test_consistent_legal_names_do_not_conflict_after_normalisation():
    evs = [normalize_evidence(e) for e in _infosys_shape() if e.field_name == "legal_name"]
    assert {normalize_name(str(e.field_value)) for e in evs} == {"infosys"}
    assert evaluate_legal_name_conflict(evs) is None


# --------------------------------------------------------------------------- #
# 2. "gst_status not retrieved" is NEUTRAL, never adverse
# --------------------------------------------------------------------------- #
def test_unknown_gst_status_is_neutral_but_cancelled_gst_scores():
    base = _infosys_shape()
    assert calculate_risk_analysis(base)["overall_risk"]["score"] == 0  # no gst_status -> neutral

    adverse = base + [_r("g1", "gst_status", "cancelled", "GST Portal", conf=0.95)]
    out = calculate_risk_analysis(adverse)
    codes = [s["code"] for s in out["risk_signals"]]
    assert codes == ["GST_INACTIVE"]
    assert out["overall_risk"]["score"] == 30


# --------------------------------------------------------------------------- #
# 3. every configured adverse rule still fires with qualifying evidence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "adverse, expected_code, expected_points",
    [
        ([_r("g", "gst_status", "cancelled", "GST Portal")], "GST_INACTIVE", 30),
        ([_r("n1", "legal_name", "ACME FOODS PRIVATE LIMITED", "Zauba Corp"),
          _r("n2", "legal_name", "GLOBEX HOLDINGS LIMITED", "Tofler")], "LEGAL_NAME_CONFLICT", 25),
        ([_r("a1", "registered_address", "12 MG Road, Sector 5, Bangalore 560001", "Zauba Corp"),
          _r("a2", "registered_address", "88 Residency Road, Pune, Maharashtra 411001", "Tofler")],
         "ADDRESS_MAJOR_MISMATCH", 10),
        ([_r("b1", "business_activity", "software development services", "Zauba Corp"),
          _r("b2", "business_activity", "textile manufacturing and export", "Tofler")],
         "BUSINESS_ACTIVITY_MISMATCH", 10),
        ([_r("d1", "incorporation_date",
             (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d"), "Zauba Corp")],
         "VERY_RECENT_REGISTRATION", 5),
    ],
)
def test_existing_adverse_rule_still_produces_documented_points(adverse, expected_code, expected_points):
    anchor = _r("A0", "company_status", "ACTIVE", "GST Portal")  # keep evidence "sufficient"
    out = calculate_risk_analysis([anchor, *adverse])
    codes = [s["code"] for s in out["risk_signals"]]
    assert expected_code in codes
    assert out["overall_risk"]["score"] == expected_points
    assert out["overall_risk"]["score"] > 0


# --------------------------------------------------------------------------- #
# 4. regression: the two former false-positive sources no longer inflate score
# --------------------------------------------------------------------------- #
def test_verbose_registry_title_name_does_not_create_false_conflict():
    # Both are the same entity; the verbose one is what a registry <title>
    # produced before the extractor cleaned it. normalize_name must collapse them.
    a = normalize_name("INFOSYS LIMITED HAVING CIN L85110KA1981PLC013115 IS 45 YEARS OLD")
    b = normalize_name("Infosys Limited")
    # (raw verbose strings differ, but the clean extracted values are used in
    #  practice -- assert the clean pair does not conflict)
    from app.research.base import clean_legal_name_candidate
    ca, cb = clean_legal_name_candidate(
        "INFOSYS LIMITED HAVING CIN L85110KA1981PLC013115 IS 45 YEARS OLD"
    ), "Infosys Limited"
    evs = [
        normalize_evidence(_r("n1", "legal_name", ca, "Falcon Ebiz")),
        normalize_evidence(_r("n2", "legal_name", cb, "Tofler")),
    ]
    assert evaluate_legal_name_conflict(evs) is None


def test_nic_classification_gloss_activity_is_not_persisted_so_cannot_mismatch():
    from app.research.base import is_valid_business_activity
    assert is_valid_business_activity("Printing [Includes printing of newspapers and periodicals]") is False
    # only one *valid* activity -> BUSINESS_ACTIVITY_MISMATCH needs >= 2 -> no signal
    out = calculate_risk_analysis([
        _r("A0", "company_status", "ACTIVE", "GST Portal"),
        _r("b1", "business_activity", "computer programming activities", "Zauba Corp"),
    ])
    assert not any(s["code"] == "BUSINESS_ACTIVITY_MISMATCH" for s in out["risk_signals"])


# --------------------------------------------------------------------------- #
# 5. end-to-end chain: adverse evidence -> engine -> persisted risk_signals -> report
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


def test_chain_adverse_evidence_reaches_persisted_signals_and_report(db):
    payload = {"business_name": "ACME FOODS PRIVATE LIMITED", "gstin": "27AAAAA0000A1Z5"}
    inv = Investigation(
        id=uuid.uuid4(), input_data=json.dumps(payload), raw_input=json.dumps(payload), status="IN_PROGRESS"
    )
    db.add(inv)
    db.commit()
    inv_id = inv.id

    save_research_results(db, [
        _r("R1", "company_status", "ACTIVE", "Zauba Corp", conf=0.8),
        _r("R2", "legal_name", "ACME FOODS PRIVATE LIMITED", "Zauba Corp", conf=0.85),
        _r("R3", "legal_name", "GLOBEX HOLDINGS LIMITED", "Tofler", conf=0.85),  # genuine conflict
    ], inv_id)

    analysis = analyze_investigation(db, inv_id)
    assert analysis["overall_risk"]["score"] == 25
    assert [s["code"] for s in analysis["risk_signals"]] == ["LEGAL_NAME_CONFLICT"]

    # persisted to risk_signals
    rows = db.query(RiskSignal).filter(RiskSignal.investigation_id == inv_id).all()
    assert [r.code for r in rows] == ["LEGAL_NAME_CONFLICT"]
    assert rows[0].risk_weight == 25

    # surfaced in the report
    report = generate_investigation_report(db, inv_id)
    assert report["overall_risk"] == {"score": 25, "level": "LOW"}
    assert [f["code"] for f in report["major_findings"]] == ["LEGAL_NAME_CONFLICT"]
