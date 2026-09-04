"""
Regression for the "wrong investigation output" bug: attempt diagnostics leaking
into final source/evidence state, stale limitations, non-target evidence
persisting, and the global "selected source" contradicting field-level evidence.

Covers cases (a)-(j) from the fix spec. No TCS-specific values are hardcoded
(the E2E test uses a generic target and generic mock pages).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.browser import BrowserResearchAgent
from app.db.base import Base
from app.graph.state import ResearchResult, ResearchTask
from app.models.browser_session import BrowserSession
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.risk.engine import calculate_risk_analysis
from app.services.evidence import save_research_results
from app.services.report import (
    build_cross_source_consistency,
    build_verification_summary,
    generate_investigation_report,
)
from app.validation.research import validate_research_results


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(name="db")
def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _mk_inv(db, gstin="27AAACX1234C1Z5"):
    inv = Investigation(
        id=uuid.uuid4(),
        input_data=json.dumps({"business_name": "SAMPLE ENTERPRISES PRIVATE LIMITED", "gstin": gstin}),
        raw_input=json.dumps({"business_name": "SAMPLE ENTERPRISES PRIVATE LIMITED", "gstin": gstin}),
        status="IN_PROGRESS",
    )
    db.add(inv)
    db.commit()
    return inv.id


def _attempt(db, inv_id, *, domain, status, source_name, url="", selected=False, order=1):
    meta = {
        "source_name": source_name,
        "url": url,
        "attempt_order": order,
        "relevance_result": status,
        "confidence": 0.0,
        "selected_as_evidence": selected,
        "failure_reason": None,
    }
    db.add(BrowserSession(
        id=uuid.uuid4(),
        investigation_id=inv_id,
        task_id="T1",
        domain=domain,
        status=status,
        action_count=1,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        failure_reason=json.dumps(meta),
    ))
    db.commit()


def _res(field_name, field_value, *, rid, source, url=None, conf=0.9, vstatus="VERIFIED"):
    return ResearchResult(
        result_id=rid, task_id="T1", field_name=field_name, field_value=field_value,
        source_name=source, source_url=url or "https://example.gov.in/x",
        retrieved_at="2026-09-02T12:00:00Z", confidence=conf, verification_status=vstatus,
    )


def _task(task_type="GST_VERIFICATION", target="SAMPLE ENTERPRISES PRIVATE LIMITED", fields=None, pref=None):
    return ResearchTask(
        task_id="T1", task_type=task_type, target=target, objective="verify",
        required_fields=fields or ["legal_name", "gst_status", "business_activity"],
        priority=1, preferred_sources=pref or ["gst.gov.in"], fallback_sources=[],
    )


# --------------------------------------------------------------------------- #
# (a) source fails then succeeds -> VERIFIED, no limitation
# --------------------------------------------------------------------------- #
def test_a_source_fails_then_succeeds_final_verified_no_limitation(db):
    inv_id = _mk_inv(db)
    save_research_results(db, [
        _res("legal_name", "SAMPLE ENTERPRISES PRIVATE LIMITED", rid="R1", source="QuickCompany", conf=0.9),
        _res("company_status", "ACTIVE", rid="R2", source="QuickCompany", conf=0.9),
    ], inv_id)
    _attempt(db, inv_id, domain="quickcompany.in", status="ERROR", source_name="QuickCompany", order=1)
    _attempt(db, inv_id, domain="quickcompany.in", status="SUCCESS", source_name="QuickCompany", order=2, selected=True)

    report = generate_investigation_report(db, inv_id)
    assert report["verification_summary"]["third_party"]["status"] == "VERIFIED"
    assert not any(lim["source"] == "Third-Party Registry" for lim in report["source_limitations"])


# --------------------------------------------------------------------------- #
# (b) all attempts fail -> UNAVAILABLE/BLOCKED + ONE deduplicated limitation
# --------------------------------------------------------------------------- #
def test_b_all_attempts_fail_single_deduplicated_limitation(db):
    inv_id = _mk_inv(db)
    # keep the entity resolvable/report non-empty via a website source
    save_research_results(db, [
        _res("website_status", "AVAILABLE", rid="W1", source="Company Website", conf=0.85),
    ], inv_id)
    for i in range(3):
        _attempt(db, inv_id, domain="gst.gov.in", status="ERROR", source_name="GST Portal", order=i + 1)

    report = generate_investigation_report(db, inv_id)
    assert report["verification_summary"]["gst"]["status"] in {"UNAVAILABLE", "BLOCKED", "NOT_FOUND", "CAPTCHA_REQUIRED"}
    gst_lims = [lim for lim in report["source_limitations"] if lim["source"] == "GST Portal"]
    assert len(gst_lims) == 1  # deduplicated by logical source


# --------------------------------------------------------------------------- #
# (c) 404 page -> zero evidence
# --------------------------------------------------------------------------- #
def test_c_404_page_yields_zero_evidence():
    html = "<html><head><title>404 Not Found</title></head><body>The requested URL was not found on this server.</body></html>"
    results = BrowserResearchAgent(fetcher=lambda u: html).execute(_task())
    assert all(r.confidence == 0.0 for r in results)
    assert validate_research_results(results).valid_results == []


# --------------------------------------------------------------------------- #
# (d) wrong GSTIN/CIN/company page -> zero evidence
# --------------------------------------------------------------------------- #
def test_d_conflicting_identifier_page_yields_zero_evidence():
    html = (
        "<html><head><title>OTHER TRADERS PRIVATE LIMITED</title></head>"
        "<body>Legal Name: OTHER TRADERS PRIVATE LIMITED  GSTIN: 09ZZZZZ9999Z9Z9  "
        "GST Status: Active  Principal Business Activity: Wholesale of textiles</body></html>"
    )
    results = BrowserResearchAgent(fetcher=lambda u: html).execute(
        _task(target="SAMPLE ENTERPRISES PRIVATE LIMITED 27AAACX1234C1Z5")
    )
    assert validate_research_results(results).valid_results == []


def test_d_wrong_company_third_party_page_yields_zero_evidence():
    html = (
        "<html><head><title>OTHER TRADERS PRIVATE LIMITED - Profile</title></head>"
        "<body>OTHER TRADERS PRIVATE LIMITED  Principal Business Activity: Printing</body></html>"
    )
    results = BrowserResearchAgent(fetcher=lambda u: html).execute(
        _task(task_type="THIRD_PARTY_RESEARCH", target="SAMPLE ENTERPRISES PRIVATE LIMITED",
              fields=["legal_name", "business_activity"], pref=["zaubacorp.com"])
    )
    assert validate_research_results(results).valid_results == []
    assert not any(str(r.field_value) == "Printing" and r.confidence > 0 for r in results)


# --------------------------------------------------------------------------- #
# (e) invalid business activity -> zero evidence   (f) valid -> persists
# --------------------------------------------------------------------------- #
def test_e_invalid_business_activity_not_persisted(db):
    inv_id = _mk_inv(db)
    bad = _res("business_activity", "Home About Us Products Services Careers Contact Login",
               rid="B1", source="Zauba Corp", conf=0.8)
    saved = save_research_results(db, [bad], inv_id)
    assert saved == [] or all(e.field_name != "business_activity" for e in saved)


def test_f_valid_business_activity_persists(db):
    inv_id = _mk_inv(db)
    good = _res("business_activity", "Computer programming, consultancy and related activities",
                rid="G1", source="MCA Portal", conf=0.9)
    saved = save_research_results(db, [good], inv_id)
    assert any(e.field_name == "business_activity" and "consultancy" in e.field_value for e in saved)


# --------------------------------------------------------------------------- #
# (g) rejected evidence excluded from reconciliation
# --------------------------------------------------------------------------- #
def test_g_rejected_evidence_excluded_from_reconciliation():
    good = _res("business_activity", "Software development services", rid="A1", source="MCA Portal", conf=0.9)
    rejected = _res("business_activity", "Printing", rid="A2", source="Zauba Corp",
                    conf=0.0, vstatus="REJECTED")
    valid = validate_research_results([good, rejected]).valid_results
    assert [r.result_id for r in valid] == ["A1"]
    rec = build_cross_source_consistency(valid, {})
    act = next(r for r in rec if r["field_key"] == "business_activity")
    assert act["status"] == "MATCH"
    assert len(act["sources_compared"]) == 1


# --------------------------------------------------------------------------- #
# (h) rejected evidence excluded from risk
# --------------------------------------------------------------------------- #
def test_h_rejected_evidence_excluded_from_risk():
    gst = _res("business_activity", "Software development services", rid="A1", source="gst.gov.in", conf=0.95)
    mca = _res("business_activity", "Software development services", rid="A2", source="MCA Portal", conf=0.9)
    rejected = _res("business_activity", "Printing", rid="A3", source="Zauba Corp", conf=0.0, vstatus="REJECTED")
    valid = validate_research_results([gst, mca, rejected]).valid_results
    analysis = calculate_risk_analysis(valid)
    assert not any(s["code"] == "BUSINESS_ACTIVITY_MISMATCH" for s in analysis["risk_signals"])


# --------------------------------------------------------------------------- #
# (i) report source status / limitations match final evidence
# --------------------------------------------------------------------------- #
def test_i_report_status_and_limitations_are_consistent(db):
    inv_id = _mk_inv(db)
    save_research_results(db, [
        _res("legal_name", "SAMPLE ENTERPRISES PRIVATE LIMITED", rid="R1", source="MCA Portal", conf=0.92),
        _res("company_status", "ACTIVE", rid="R2", source="MCA Portal", conf=0.92),
    ], inv_id)
    _attempt(db, inv_id, domain="mca.gov.in", status="SUCCESS", source_name="MCA Portal", selected=True)
    _attempt(db, inv_id, domain="gst.gov.in", status="ERROR", source_name="GST Portal")
    _attempt(db, inv_id, domain="epfindia.gov.in", status="BLOCKED_OR_ERROR", source_name="EPFO Portal")

    report = generate_investigation_report(db, inv_id)
    vs = report["verification_summary"]
    lims = report["source_limitations"]

    verified_cats = {c for c, d in vs.items() if d["status"] == "VERIFIED"}
    limited_sources = {lim["source"] for lim in lims}
    from app.services.report import _SOURCE_CATEGORY_LABELS
    # no source is both VERIFIED and limited
    assert not (verified_cats & {c for c, lbl in _SOURCE_CATEGORY_LABELS.items() if lbl in limited_sources})
    # every limitation corresponds to a non-verified, attempted source
    for lim in lims:
        assert lim["status"] in {"UNAVAILABLE", "BLOCKED", "NOT_FOUND", "CAPTCHA_REQUIRED"}
    # MCA is verified and never limited
    assert vs["mca"]["status"] == "VERIFIED"
    assert "MCA Portal" not in limited_sources


# --------------------------------------------------------------------------- #
# (j) global selected source agrees with field-level evidence
# --------------------------------------------------------------------------- #
def test_j_primary_source_matches_field_level_evidence(db):
    inv_id = _mk_inv(db)
    save_research_results(db, [
        _res("legal_name", "SAMPLE ENTERPRISES PRIVATE LIMITED", rid="R1", source="MCA Portal", conf=0.93),
        _res("business_activity", "Software development services", rid="R2", source="QuickCompany", conf=0.8),
    ], inv_id)
    report = generate_investigation_report(db, inv_id)

    ev_sources = {e["source_name"] for e in report["evidence_summary"] if (e["confidence"] or 0) >= 0.5}
    assert report["primary_source"] in ev_sources
    # highest-authority persisted source wins (MCA over a third-party registry)
    assert report["primary_source"] == "MCA Portal"


# --------------------------------------------------------------------------- #
# generic E2E: final source state / evidence / report all agree
# --------------------------------------------------------------------------- #
def test_e2e_final_state_is_internally_consistent(db):
    from unittest import mock
    from app.graph.workflow import app as graph_app

    raw_input = {"business_name": "SAMPLE ENTERPRISES PRIVATE LIMITED", "gstin": "27AAACX1234C1Z5"}
    inv = Investigation(input_data=json.dumps(raw_input), raw_input=json.dumps(raw_input))
    db.add(inv)
    db.commit()
    db.refresh(inv)
    inv_id = inv.id

    class MockSessionLocal:
        def __enter__(self):
            return db
        def __exit__(self, *a):
            pass

    def fetch(url: str) -> str:
        u = url.lower()
        if "gst.gov.in" in u:
            # GST fetch "succeeds" (page loads) but carries NO usable field data
            return "<html><title>Goods &amp; Services Tax</title><body>Search Taxpayer</body></html>"
        if "mca.gov.in" in u:
            return "<html><title>503 Service Unavailable</title><body>Service Temporarily Unavailable</body></html>"
        if "zaubacorp" in u or "tofler" in u or "quickcompany" in u or "instafinancials" in u:
            # a WRONG company profile — must not contribute a business activity
            return ("<html><title>OTHER TRADERS PRIVATE LIMITED</title>"
                    "<body>OTHER TRADERS PRIVATE LIMITED  Principal Business Activity: Printing</body></html>")
        if "epfindia.gov.in" in u:
            return "<html><title>Access Denied</title><body>403 Forbidden</body></html>"
        return "<html><title>404 Not Found</title><body>Page not found</body></html>"

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(fetch)):
        graph_app.invoke({
            "investigation_id": str(inv_id),
            "raw_input": raw_input,
            "normalized_input": {},
            "pending_tasks": [], "completed_tasks": [], "failed_tasks": [], "results": [],
            "planner_loop_count": 0, "qa_loop_count": 0, "status": "CREATED",
        })

    report = generate_investigation_report(db, inv_id)

    # no "Printing" anywhere in persisted evidence / reconciliation / findings
    ev_blob = json.dumps(report["evidence_summary"]) + json.dumps(report["cross_source_consistency"])
    assert "Printing" not in ev_blob

    # verification_summary <-> source_limitations agree
    from app.services.report import _SOURCE_CATEGORY_LABELS
    limited = {lim["source"] for lim in report["source_limitations"]}
    for cat, data in report["verification_summary"].items():
        label = _SOURCE_CATEGORY_LABELS.get(cat, cat)
        if data["status"] == "VERIFIED":
            assert label not in limited
        if label in limited:
            assert data["status"] in {"UNAVAILABLE", "BLOCKED", "NOT_FOUND", "CAPTCHA_REQUIRED"}

    # every source_limitations entry appears at most once (deduplicated)
    assert len(limited) == len(report["source_limitations"])

    # global primary source (if any) is backed by real field-level evidence
    if report["primary_source"] is not None:
        ev_sources = {e["source_name"] for e in report["evidence_summary"] if (e["confidence"] or 0) >= 0.5}
        assert report["primary_source"] in ev_sources
