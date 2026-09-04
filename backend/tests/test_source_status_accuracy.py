"""
Source-status accuracy.

Traces the attempt outcome for each source through:
  browser/HTTP outcome -> ResearchResult -> browser_sessions -> evidence
  persistence -> final source status -> report.

Guarantees:
  * BLOCKED_OR_ERROR only for a genuinely blocked / failed fetch.
  * A page that opens and yields usable target-company info is SUCCESS.
  * A later blocked attempt never demotes an earlier SUCCESS.
  * A SUCCESS on a fallback URL is never demoted because the primary URL failed.
  * REJECTED / ENTITY_MISMATCH / UNRELATED stay distinct from blocked.
  * Every individual attempt is still stored for diagnostics.
  * The per-source FINAL status = best outcome across all attempts.
  * TASK_COMPLETED never automatically means SUCCESS / VERIFIED.

No company-specific values are hardcoded.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.graph.state import ResearchResult, ResearchTask
from app.graph.nodes import _reconcile_selected_as_evidence
from app.models.browser_session import BrowserSession
from app.models.investigation import Investigation
from app.agents.browser import BrowserResearchAgent
from app.services.evidence import save_research_results
from app.services.report import (
    _classify_source_status,
    derive_browser_source_statuses,
    generate_investigation_report,
)


# --------------------------------------------------------------------------- #
# fixtures / helpers
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


def _mk_inv(db, cin="U72200MH2005PTC152123"):
    payload = {"business_name": "SAMPLE ENTERPRISES PRIVATE LIMITED", "cin": cin}
    inv = Investigation(
        id=uuid.uuid4(), input_data=json.dumps(payload), raw_input=json.dumps(payload),
        status="IN_PROGRESS",
    )
    db.add(inv)
    db.commit()
    return inv.id


def _attempt(db, inv_id, *, domain, status, source_name, url="", selected=False, order=1):
    meta = {
        "source_name": source_name, "url": url, "attempt_order": order,
        "relevance_result": status, "confidence": 0.0, "selected_as_evidence": selected,
    }
    db.add(BrowserSession(
        id=uuid.uuid4(), investigation_id=inv_id, task_id="T1", domain=domain,
        status=status, action_count=1, started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc), failure_reason=json.dumps(meta),
    ))
    db.commit()


def _res(field_name, field_value, *, rid, source, url=None, conf=0.9, vstatus="VERIFIED"):
    return ResearchResult(
        result_id=rid, task_id="T1", field_name=field_name, field_value=field_value,
        source_name=source, source_url=url or "https://example.test/x",
        retrieved_at="2026-09-04T12:00:00Z", confidence=conf, verification_status=vstatus,
    )


def _rows(*pairs):
    """(domain, status) tuples -> objects derive_browser_source_statuses accepts."""
    return [type("S", (), {"domain": d, "status": s})() for d, s in pairs]


# --------------------------------------------------------------------------- #
# A. per-source rollup = best outcome across all attempts  (7 scenarios)
# --------------------------------------------------------------------------- #
def test_scenario_1_zauba_success_then_blocked_stays_success():
    out = derive_browser_source_statuses(
        _rows(("zaubacorp.com", "SUCCESS"), ("zaubacorp.com", "BLOCKED_OR_ERROR"))
    )
    assert out["zaubacorp.com"]["status"] == "SUCCESS"
    assert out["zaubacorp.com"]["attempts"] == 2  # both attempts retained


def test_scenario_2_tofler_blocked_url_then_success_url_is_success():
    out = derive_browser_source_statuses(
        _rows(("tofler.in", "BLOCKED_OR_ERROR"), ("tofler.in", "SUCCESS"))
    )
    assert out["tofler.in"]["status"] == "SUCCESS"


def test_scenario_3_quickcompany_blocked_then_success_is_success():
    out = derive_browser_source_statuses(
        _rows(("quickcompany.in", "BLOCKED_OR_ERROR"), ("quickcompany.in", "SUCCESS"))
    )
    assert out["quickcompany.in"]["status"] == "SUCCESS"


def test_scenario_4_instafinancials_captcha_then_alternative():
    # with a successful alternative
    with_alt = derive_browser_source_statuses(
        _rows(("instafinancials.com", "BLOCKED_OR_ERROR"),
              ("instafinancials.com", "CAPTCHA_REQUIRED"),
              ("instafinancials.com", "SUCCESS"))
    )
    assert with_alt["instafinancials.com"]["status"] == "SUCCESS"
    # without any successful alternative -> stays blocked
    no_alt = derive_browser_source_statuses(
        _rows(("instafinancials.com", "CAPTCHA_REQUIRED"),
              ("instafinancials.com", "BLOCKED_OR_ERROR"))
    )
    assert no_alt["instafinancials.com"]["status"] in {"CAPTCHA_REQUIRED", "BLOCKED_OR_ERROR"}


def test_scenario_5_falcon_success_then_blocked_alternative_stays_success():
    out = derive_browser_source_statuses(
        _rows(("falconebiz.com", "SUCCESS"), ("falconebiz.com", "BLOCKED_OR_ERROR"))
    )
    assert out["falconebiz.com"]["status"] == "SUCCESS"


def test_scenario_6_genuine_blocked_only_source_stays_blocked():
    out = derive_browser_source_statuses(
        _rows(("instafinancials.com", "BLOCKED_OR_ERROR"),
              ("instafinancials.com", "BLOCKED_OR_ERROR"),
              ("instafinancials.com", "BLOCKED_OR_ERROR"))
    )
    assert out["instafinancials.com"]["status"] == "BLOCKED_OR_ERROR"


def test_scenario_7_rejected_wrong_entity_stays_rejected_not_blocked():
    out = derive_browser_source_statuses(
        _rows(("generic_web", "REJECTED"), ("generic_web", "REJECTED"))
    )
    assert out["generic_web"]["status"] == "REJECTED"
    # a rejected page mixed with a blocked one still reads as REJECTED, not blocked
    mixed = derive_browser_source_statuses(
        _rows(("generic_web", "REJECTED"), ("generic_web", "BLOCKED_OR_ERROR"))
    )
    assert mixed["generic_web"]["status"] == "REJECTED"


def test_rollup_empty_and_success_beats_everything():
    assert derive_browser_source_statuses([]) == {}
    out = derive_browser_source_statuses(
        _rows(("x.com", "ERROR"), ("x.com", "REJECTED"),
              ("x.com", "BLOCKED_OR_ERROR"), ("x.com", "SUCCESS"), ("x.com", "NO_DATA"))
    )
    assert out["x.com"]["status"] == "SUCCESS"
    assert out["x.com"]["attempts"] == 5


# --------------------------------------------------------------------------- #
# B. report exposes the per-source final status
# --------------------------------------------------------------------------- #
def test_report_source_attempt_status_reflects_best_outcome(db):
    inv_id = _mk_inv(db)
    save_research_results(db, [
        _res("legal_name", "SAMPLE ENTERPRISES PRIVATE LIMITED", rid="R1", source="Zauba Corp"),
        _res("company_status", "ACTIVE", rid="R2", source="Zauba Corp"),
    ], inv_id)
    _attempt(db, inv_id, domain="zaubacorp.com", status="SUCCESS", source_name="Zauba Corp", order=1, selected=True)
    _attempt(db, inv_id, domain="zaubacorp.com", status="BLOCKED_OR_ERROR", source_name="Zauba Corp", order=2)
    _attempt(db, inv_id, domain="instafinancials.com", status="BLOCKED_OR_ERROR", source_name="InstaFinancials", order=1)
    _attempt(db, inv_id, domain="instafinancials.com", status="CAPTCHA_REQUIRED", source_name="InstaFinancials", order=2)
    _attempt(db, inv_id, domain="generic_web", status="REJECTED", source_name="General Web", order=1)

    report = generate_investigation_report(db, inv_id)
    sas = report["source_attempt_status"]
    assert sas["zaubacorp.com"]["status"] == "SUCCESS"
    assert sas["instafinancials.com"]["status"] in {"BLOCKED_OR_ERROR", "CAPTCHA_REQUIRED"}
    assert sas["generic_web"]["status"] == "REJECTED"
    # every attempt still individually stored
    assert sas["zaubacorp.com"]["attempts"] == 2
    # the evidence-backed source stays third_party VERIFIED, unaffected
    assert report["verification_summary"]["third_party"]["status"] == "VERIFIED"


# --------------------------------------------------------------------------- #
# C. a page that opens but yields no evidence must not read as SUCCESS
# --------------------------------------------------------------------------- #
def test_reconcile_downgrades_opened_no_evidence_attempt_to_no_data(db):
    inv_id = _mk_inv(db)
    # Zauba really produced evidence; GST portal landing produced nothing.
    save_research_results(db, [
        _res("legal_name", "SAMPLE ENTERPRISES PRIVATE LIMITED", rid="R1", source="Zauba Corp"),
    ], inv_id)
    _attempt(db, inv_id, domain="zaubacorp.com", status="SUCCESS", source_name="Zauba Corp",
             url="https://www.zaubacorp.com/company/x", order=1)
    _attempt(db, inv_id, domain="gst.gov.in", status="SUCCESS", source_name="GST Portal",
             url="https://services.gst.gov.in/services/searchtp", order=1)

    _reconcile_selected_as_evidence(db, inv_id)
    db.expire_all()
    rows = {r.domain: r for r in db.query(BrowserSession).filter(BrowserSession.investigation_id == inv_id)}
    assert rows["zaubacorp.com"].status == "SUCCESS"       # had evidence -> stays
    assert rows["gst.gov.in"].status == "NO_DATA"          # opened, no evidence -> downgraded


# --------------------------------------------------------------------------- #
# D. _classify_source_status: verified beats a stray blocked/captcha marker
# --------------------------------------------------------------------------- #
def _ev(fn, fv, conf=0.8, vs="VERIFIED"):
    return type("E", (), {"field_name": fn, "field_value": fv, "confidence": conf, "verification_status": vs})()


def test_classify_verified_is_not_demoted_by_captcha_or_blocked_marker():
    evs = [
        _ev("legal_name", "SAMPLE ENTERPRISES PRIVATE LIMITED", 0.8, "VERIFIED"),
        _ev("business_activity", "captcha and human verification services", 0.8, "VERIFIED"),
        _ev("page_text", "BLOCKED", 0.6, "UNVERIFIED"),
    ]
    status, _ = _classify_source_status(evs)
    assert status == "VERIFIED"


def test_classify_blocked_only_still_blocked_and_captcha_only_still_captcha():
    assert _classify_source_status([_ev("page_text", "BLOCKED", 0.6, "UNVERIFIED")])[0] == "BLOCKED"
    assert _classify_source_status([_ev("captcha_notice", "solve captcha", 0.6, "UNVERIFIED")])[0] == "CAPTCHA_REQUIRED"


# --------------------------------------------------------------------------- #
# E. execute(): a fallback URL that opens is used even though the primary failed
# --------------------------------------------------------------------------- #
def test_execute_uses_successful_fallback_url_when_primary_url_fails():
    target = "SAMPLE ENTERPRISES PRIVATE LIMITED U72200MH2005PTC152123"
    good_page = (
        "<html><head><title>SAMPLE ENTERPRISES PRIVATE LIMITED</title></head><body>"
        "<h1>SAMPLE ENTERPRISES PRIVATE LIMITED</h1>"
        "<p>CIN: U72200MH2005PTC152123</p>"
        "<p>Company Status: Active</p>"
        "<p>Registered Address: 21 Trade Centre, Andheri East, Mumbai 400069</p>"
        "<p>Principal Business Activity: Business support service activities</p></body></html>"
    )
    calls = []

    def fetcher(url):
        calls.append(url)
        # first candidate URL is Cloudflare-blocked; anything else opens fine
        if len(calls) == 1:
            return "<html><head><title>Attention Required! | Cloudflare</title></head><body>cf-browser-verification</body></html>"
        return good_page

    task = ResearchTask(
        task_id="T-ZB", task_type="THIRD_PARTY_RESEARCH", target=target,
        objective="verify", required_fields=["legal_name", "company_status", "registered_address"],
        priority=2, preferred_sources=["zaubacorp.com"], fallback_sources=[],
    )
    results = BrowserResearchAgent(fetcher=fetcher).execute(task)

    assert len(calls) >= 2, "the blocked primary URL should have been followed by a fallback"
    graded = [r for r in results if (r.confidence or 0) > 0]
    assert graded, "the successful fallback URL's data must be used, not downgraded"
    by_field = {r.field_name: r for r in graded}
    assert by_field["company_status"].field_value.upper().startswith("ACTIVE")
    assert by_field["legal_name"].field_value.upper().startswith("SAMPLE ENTERPRISES")


def test_execute_fully_blocked_source_yields_only_zero_confidence_results():
    def fetcher(url):
        return "<html><head><title>Access Denied</title></head><body>403 forbidden</body></html>"

    task = ResearchTask(
        task_id="T-IF", task_type="THIRD_PARTY_RESEARCH",
        target="SAMPLE ENTERPRISES PRIVATE LIMITED U72200MH2005PTC152123",
        objective="verify", required_fields=["legal_name", "company_status"],
        priority=2, preferred_sources=["instafinancials.com"], fallback_sources=[],
    )
    results = BrowserResearchAgent(fetcher=fetcher).execute(task)
    # nothing usable -> a TASK_COMPLETED event for this would be NO_DATA, not SUCCESS
    assert all((r.confidence or 0.0) == 0.0 for r in results)
