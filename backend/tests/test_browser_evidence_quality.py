"""
Focused tests for the generic Browser Research -> evidence quality guards:

  1. page relevance validation before extraction
  2. entity identity validation before evidence persistence
  3. generic business-activity semantic validation
  4. rejected page/evidence never reaches reconciliation / risk / factual report
  5. discovery / attempt diagnostics preserved separately from factual evidence
  6. wrong-company pages, search snippets, navigation text, conflicting
     identifiers, unrelated business activity

All checks are structural / generic -- no company names, URLs, identifiers, or
fixed "bad phrase" lists are used by the guards under test.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.browser import BrowserResearchAgent
from app.db.base import Base
from app.graph.state import ResearchResult, ResearchTask
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.research.base import is_valid_business_activity, page_conflicts_with_target
from app.risk.engine import calculate_risk_analysis
from app.services.evidence import save_research_results
from app.services.report import build_cross_source_consistency
from app.validation.research import validate_research_result, validate_research_results


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


def _result(field_name, field_value, *, rid="R1", source="GST Portal", conf=0.9, vstatus="VERIFIED"):
    return ResearchResult(
        result_id=rid,
        task_id="T1",
        field_name=field_name,
        field_value=field_value,
        source_name=source,
        source_url="https://example.gov.in/x",
        retrieved_at="2026-09-02T12:00:00Z",
        confidence=conf,
        verification_status=vstatus,
    )


def _task(task_type="GST_VERIFICATION", target="ALPHA FOODS PRIVATE LIMITED", fields=None, pref=None, fb=None):
    return ResearchTask(
        task_id="T1",
        task_type=task_type,
        target=target,
        objective="verify",
        required_fields=fields or ["legal_name", "gst_status", "business_activity"],
        priority=1,
        preferred_sources=pref or ["gst.gov.in"],
        fallback_sources=fb or [],
    )


# --------------------------------------------------------------------------- #
# 3 + 6. Generic business-activity semantic validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value",
    [
        # navigation menus
        "Home About Us Products Services Careers Contact Login",
        "Home | Products | Services | Support | Company | Contact",
        "Dashboard  ›  Companies  ›  Directors  ›  Charges  ›  Documents",
        # search snippets
        "Best match for your query - see results and more on the web …",
        "What is the principal business activity of this company?",
        "Company profile, financials and directors - full report available at ...",
        # page dump / multi-field
        "Legal Name: X\nStatus: Active\nCIN: U74999MH2010PLC000001\nActivity: trading",
        # markup / url / identifier
        "<div class='nav'>Business Activity</div>",
        "visit www.example.com/activity for details",
        "U74999MH2010PLC000001",
        # bare field label
        "Nature of Business Activities:",
        "Principal Business Activity",
    ],
)
def test_invalid_business_activity_strings_are_rejected(value):
    assert is_valid_business_activity(value) is False
    res = _result("business_activity", value)
    assert validate_research_result(res).is_valid is False


@pytest.mark.parametrize(
    "value",
    [
        "Software",
        "IT services",
        "Wholesale trade",
        "Manufacture of electrical equipment",
        "Computer programming, consultancy and related activities",
        "Real estate development and property management",
        "Information technology consultancy services",
    ],
)
def test_valid_business_activity_strings_are_accepted(value):
    assert is_valid_business_activity(value) is True
    assert validate_research_result(_result("business_activity", value)).is_valid is True


# --------------------------------------------------------------------------- #
# 1 + 2 + 6. Page relevance / entity identity: wrong-company & conflicting ids
# --------------------------------------------------------------------------- #
def test_page_conflicts_on_conflicting_gstin():
    # target carries one GSTIN, page shows a different one
    assert page_conflicts_with_target(
        "27AAAAA0000A1Z5",
        "Some Registry Result",
        "GSTIN: 09BBBBB1111B1Z9  Status: Active",
    ) is True
    # same GSTIN, hyphen/space tolerant -> NOT a conflict
    assert page_conflicts_with_target(
        "27AAAAA0000A1Z5",
        "Whatever Ltd",
        "GSTIN of Taxpayer: 27-AAAAA0000A-1Z5",
    ) is False


def test_page_conflicts_on_wrong_named_company():
    assert page_conflicts_with_target(
        "ALPHA FOODS PRIVATE LIMITED",
        "BETA TRADERS PRIVATE LIMITED - Company Profile",
        "BETA TRADERS PRIVATE LIMITED is engaged in wholesale trade.",
    ) is True
    # correct company -> not a conflict
    assert page_conflicts_with_target(
        "ALPHA FOODS PRIVATE LIMITED",
        "ALPHA FOODS PRIVATE LIMITED",
        "ALPHA FOODS PRIVATE LIMITED, incorporated 2011.",
    ) is False
    # identifier-only target must not be flagged just because a name is on the page
    assert page_conflicts_with_target(
        "27ABCDE1234F1Z5",
        "Acme Foods Corp",
        "GST Active",
    ) is False


def test_wrong_company_page_yields_no_factual_evidence():
    html = (
        "<html><head><title>BETA TRADERS PRIVATE LIMITED - Profile</title></head>"
        "<body>Legal Name: BETA TRADERS PRIVATE LIMITED<br>GST Status: Active<br>"
        "Principal Business Activity: Wholesale trade of textiles</body></html>"
    )
    agent = BrowserResearchAgent(fetcher=lambda url: html)
    results = agent.execute(_task(task_type="GST_VERIFICATION", target="ALPHA FOODS PRIVATE LIMITED"))

    # every field is rejected / not-found and carries zero confidence
    for r in results:
        assert r.confidence == 0.0
        assert r.verification_status in {"REJECTED", "SOURCE_UNAVAILABLE"}
    # and none of it would persist
    assert validate_research_results(results).valid_results == []


def test_correct_company_page_still_extracts_and_persists(db):
    # regression guard: the relevance/identity gate must NOT fire for the real
    # target entity (title names the target, no conflicting identifier).
    html = """
    <html>
    <head><title>ALPHA FOODS PRIVATE LIMITED</title></head>
    <body>
        <h1>ALPHA FOODS PRIVATE LIMITED</h1>
        <p>Legal Name: ALPHA FOODS PRIVATE LIMITED</p>
        <p>GST Status: Active</p>
        <p>Registered Address: 12 Industrial Estate, MIDC, Pune, Maharashtra 411018</p>
        <p>Principal Business Activity: Manufacture of processed foods</p>
    </body>
    </html>
    """
    agent = BrowserResearchAgent(fetcher=lambda url: html)
    results = agent.execute(
        _task(
            task_type="GST_VERIFICATION",
            target="ALPHA FOODS",
            fields=["legal_name", "gst_status", "business_activity"],
        )
    )
    by_field = {r.field_name: r for r in results}
    assert by_field["legal_name"].field_value == "ALPHA FOODS PRIVATE LIMITED"
    assert by_field["legal_name"].verification_status != "REJECTED"
    assert by_field["business_activity"].field_value == "Manufacture of processed foods"
    assert by_field["business_activity"].verification_status == "VERIFIED"

    inv_id = uuid.uuid4()
    db.add(Investigation(id=inv_id, input_data='{"business_name": "ALPHA FOODS PRIVATE LIMITED"}', status="IN_PROGRESS"))
    db.commit()
    saved = save_research_results(db, results, inv_id)
    saved_fields = {e.field_name for e in saved}
    assert "legal_name" in saved_fields
    assert "business_activity" in saved_fields


# --------------------------------------------------------------------------- #
# 4. Rejected evidence never reaches persistence / reconciliation / risk / report
# --------------------------------------------------------------------------- #
def test_rejected_verification_status_never_persists(db):
    inv_id = uuid.uuid4()
    db.add(Investigation(id=inv_id, input_data='{"business_name": "X"}', status="IN_PROGRESS"))
    db.commit()

    good = _result("business_activity", "Manufacture of electrical equipment", rid="G1", source="MCA Portal")
    rejected = _result(
        "business_activity",
        "Manufacture of electrical equipment",   # shape-valid, but from a wrong-entity page
        rid="B1",
        source="GST Portal",
        vstatus="REJECTED",
    )
    nav = _result("business_activity", "Home Products Services Careers Contact Login", rid="B2", source="General Web")

    saved = save_research_results(db, [good, rejected, nav], inv_id)
    persisted = db.query(Evidence).filter(Evidence.investigation_id == inv_id).all()
    assert {e.research_result_id for e in persisted} == {"G1"}
    assert len(saved) == 1


def test_rejected_evidence_excluded_from_reconciliation_and_risk():
    good = _result("business_activity", "Wholesale trade of electronics", rid="A1", source="MCA Portal")
    contaminated = _result(
        "business_activity",
        "Search results for company activity - see more on the web …",
        rid="A2",
        source="General Web",
    )
    valid = validate_research_results([good, contaminated])
    assert [r.result_id for r in valid.valid_results] == ["A1"]

    rec = build_cross_source_consistency(valid.valid_results, {})
    act = next(r for r in rec if r["field_key"] == "business_activity")
    assert act["status"] == "MATCH"                      # only the one clean source
    assert len(act["sources_compared"]) == 1

    # a mismatch signal must not be produced from the contaminated value
    gst = _result("business_activity", "Wholesale trade of electronics", rid="A1", source="gst.gov.in")
    analysis = calculate_risk_analysis(valid.valid_results + [gst])
    assert not any(s["code"] == "BUSINESS_ACTIVITY_MISMATCH" for s in analysis["risk_signals"])


# --------------------------------------------------------------------------- #
# 5. Attempt / discovery diagnostics stay separate from factual evidence
# --------------------------------------------------------------------------- #
def test_rejected_page_records_attempt_diagnostic_but_no_evidence():
    """A wrong-entity page is recorded as an attempt diagnostic (BrowserSession)
    but produces zero factual Evidence rows. Uses the application DB session, like
    the other DB-backed browser tests in this suite."""
    from app.db.session import SessionLocal
    from app.models.browser_session import BrowserSession

    inv_id = uuid.uuid4()
    with SessionLocal() as s:
        s.add(Investigation(id=inv_id, input_data='{"business_name": "ALPHA FOODS PRIVATE LIMITED"}', status="IN_PROGRESS"))
        s.commit()
    try:
        html = (
            "<html><head><title>BETA TRADERS PRIVATE LIMITED</title></head>"
            "<body>GSTIN: 09BBBBB1111B1Z9  Legal Name: BETA TRADERS PRIVATE LIMITED  "
            "Principal Business Activity: Wholesale trade</body></html>"
        )
        agent = BrowserResearchAgent(fetcher=lambda url: html)
        results = agent.execute(
            _task(task_type="GST_VERIFICATION", target="ALPHA FOODS PRIVATE LIMITED"),
            investigation_id=inv_id,
        )
        with SessionLocal() as s:
            save_research_results(s, results, inv_id)
            sessions = s.query(BrowserSession).filter(BrowserSession.investigation_id == inv_id).all()
            assert any(
                bs.status in {"REJECTED", "BLOCKED_OR_ERROR", "ERROR", "IRRELEVANT_CONTENT"}
                for bs in sessions
            )
            assert s.query(Evidence).filter(Evidence.investigation_id == inv_id).count() == 0
    finally:
        with SessionLocal() as s:
            from app.models.browser_session import BrowserSession as BS
            from app.models.investigation_event import InvestigationEvent as IE
            s.query(Evidence).filter(Evidence.investigation_id == inv_id).delete()
            s.query(BS).filter(BS.investigation_id == inv_id).delete()
            s.query(IE).filter(IE.investigation_id == inv_id).delete()
            s.query(Investigation).filter(Investigation.id == inv_id).delete()
            s.commit()


# --------------------------------------------------------------------------- #
# 6. Search-snippet / navigation text as legal name is also rejected end-to-end
# --------------------------------------------------------------------------- #
def test_search_snippet_and_nav_text_rejected_as_legal_name(db):
    inv_id = uuid.uuid4()
    db.add(Investigation(id=inv_id, input_data='{"business_name": "X"}', status="IN_PROGRESS"))
    db.commit()

    snippet = _result("legal_name", "Search results for company registration in Maharashtra", rid="S1")
    nav = _result("legal_name", "Home About Us Contact Login Register", rid="S2", source="General Web")
    clean = _result("legal_name", "GAMMA INDUSTRIES LIMITED", rid="S3", source="MCA Portal")

    saved = save_research_results(db, [snippet, nav, clean], inv_id)
    assert {e.research_result_id for e in saved} == {"S3"}
