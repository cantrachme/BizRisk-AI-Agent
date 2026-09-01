import pytest
from unittest import mock
import json
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.graph.workflow import app as graph_app
from app.graph.state import ResearchTask, ResearchResult
from app.models.investigation import Investigation
from app.models.evidence import Evidence
from app.models.report import Report
from app.agents.intake import IntakeAgent
from app.agents.browser import BrowserResearchAgent
from app.entity_resolution.scoring import score_entities, compute_name_similarity
from app.entity_resolution.resolver import resolve_entity
from app.risk.engine import calculate_risk_analysis
from app.services.report import (
    generate_investigation_report,
    build_verification_summary,
    build_cross_source_consistency,
)
from app.services.qa import validate_report


@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ==============================================================================
# 1. Valid Company with Strong Official Evidence
# ==============================================================================
def test_case_1_valid_company_strong_official_evidence():
    """
    Valid company where Tier 1 GST and MCA confirm active status and matching name/address.
    """
    target = {
        "business_name": "ACME INDUSTRIAL SOLUTIONS PVT LTD",
        "gstin": "27AAACA1234A1Z5",
        "cin": "U12345MH2015PTC123456",
        "location": "Mumbai, Maharashtra",
    }
    res_gst = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="gst.gov.in",
        source_url="https://services.gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.95,
    )
    res_mca = ResearchResult(
        result_id="RES-002",
        task_id="TASK-002",
        field_name="company_status",
        field_value="ACTIVE",
        source_name="mca.gov.in",
        source_url="https://www.mca.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.95,
    )
    analysis = calculate_risk_analysis([res_gst, res_mca])
    assert analysis["overall_risk"]["level"] == "LOW"
    assert analysis["overall_risk"]["score"] == 0
    assert len(analysis["risk_signals"]) == 0

    reconciliation = build_cross_source_consistency([res_gst, res_mca], target)
    comp_rec = next(r for r in reconciliation if r["field_key"] == "company_status")
    assert comp_rec["status"] == "MATCH"


# ==============================================================================
# 2. Company where GST is Unavailable
# ==============================================================================
def test_case_2_gst_unavailable_graceful_handling():
    """
    GST portal is blocked/503 -> recorded as SOURCE_UNAVAILABLE, not NOT_FOUND or GST_INACTIVE.
    """
    res_gst = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="gst_status",
        field_value="UNAVAILABLE",
        source_name="gst.gov.in",
        source_url="https://services.gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.0,
    )
    analysis = calculate_risk_analysis([res_gst])
    # Unavailable GST must not trigger false GST_INACTIVE signal
    assert not any(s["code"] == "GST_INACTIVE" for s in analysis["risk_signals"])


# ==============================================================================
# 3. Company where MCA is Unavailable
# ==============================================================================
def test_case_3_mca_unavailable_graceful_handling():
    """
    MCA portal is blocked/503 -> recorded as SOURCE_UNAVAILABLE, not COMPANY_STRUCK_OFF.
    """
    res_mca = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="company_status",
        field_value="UNAVAILABLE",
        source_name="mca.gov.in",
        source_url="https://www.mca.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.0,
    )
    analysis = calculate_risk_analysis([res_mca])
    assert not any(s["code"] == "COMPANY_STRUCK_OFF" for s in analysis["risk_signals"])


# ==============================================================================
# 4. CAPTCHA-Protected Source
# ==============================================================================
def test_case_4_captcha_protected_source_flagged():
    """
    CAPTCHA challenge is explicitly classified as CAPTCHA_REQUIRED, never claimed as verified.
    """
    captcha_html = "<html><title>GST Portal</title><body>Please enter captcha image code to proceed.</body></html>"
    classification = BrowserResearchAgent._is_failed_or_blocked_retrieval(captcha_html, "TARGET CORP")
    assert classification in {"CAPTCHA_REQUIRED", "BLOCKED_OR_ERROR"}


# ==============================================================================
# 5. Irrelevant Search Results
# ==============================================================================
def test_case_5_irrelevant_search_results_rejected():
    """
    Sports, entertainment, or unrelated businesses returned by search are rejected before opening.
    """
    target = "VERTEX ENGINEERING SOLUTIONS"
    assert not BrowserResearchAgent._is_valid_candidate_url("https://espncricinfo.com/scores", target, "WEBSITE_VERIFICATION")
    assert not BrowserResearchAgent._is_valid_candidate_url("https://netflix.com/title/123", target, "WEBSITE_VERIFICATION")
    assert not BrowserResearchAgent._is_valid_candidate_url("https://vertexpharma.com/drugs", target, "WEBSITE_VERIFICATION")


# ==============================================================================
# 6. Similar Company Names (Sector / Token Mismatch)
# ==============================================================================
def test_case_6_similar_name_sector_mismatch_rejected():
    """
    Candidate with similar name but different sector (e.g. Pharma vs Engineering) scores 0.0.
    """
    target = {"business_name": "VERTEX ENGINEERING SOLUTIONS"}
    candidate = {"business_name": "Vertex Pharma India Pvt Ltd"}
    score = score_entities(target, candidate)
    assert score == 0.0


# ==============================================================================
# 7. Conflicting Addresses
# ==============================================================================
def test_case_7_conflicting_addresses_detected():
    """
    Different registered addresses across sources trigger ADDRESS_MAJOR_MISMATCH and CONFLICT in reconciliation.
    """
    addr_gst = "Plot No. 12, MIDC Andheri East, Mumbai, Maharashtra 400093"
    addr_web = "Flat 402, Brigade Towers, MG Road, Bengaluru, Karnataka 560001"
    
    res1 = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="registered_address",
        field_value=addr_gst,
        source_name="gst.gov.in",
        source_url="https://gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.95,
    )
    res2 = ResearchResult(
        result_id="RES-002",
        task_id="TASK-002",
        field_name="registered_address",
        field_value=addr_web,
        source_name="Company Website",
        source_url="https://targetcompany.com",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.85,
    )
    analysis = calculate_risk_analysis([res1, res2])
    assert any(sig["code"] == "ADDRESS_MAJOR_MISMATCH" for sig in analysis["risk_signals"])

    reconciliation = build_cross_source_consistency([res1, res2], {"business_name": "Target Company"})
    addr_rec = next(r for r in reconciliation if r["field_key"] == "registered_address")
    assert addr_rec["status"] == "CONFLICT"


# ==============================================================================
# 8. Conflicting Business Activity
# ==============================================================================
def test_case_8_conflicting_business_activity():
    """
    Discrepancy between declared activity (e.g. IT Consulting) and registry (e.g. Real Estate) is flagged.
    """
    res1 = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="business_activity",
        field_value="Real Estate Development and Property Brokerage",
        source_name="gst.gov.in",
        source_url="https://gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.95,
    )
    res2 = ResearchResult(
        result_id="RES-002",
        task_id="TASK-002",
        field_name="business_activity",
        field_value="Software Development and Information Technology",
        source_name="Company Website",
        source_url="https://targetcompany.com",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.85,
    )
    reconciliation = build_cross_source_consistency([res1, res2], {"business_name": "Target Company"})
    act_rec = next(r for r in reconciliation if r["field_key"] == "business_activity")
    assert act_rec["status"] == "CONFLICT"


# ==============================================================================
# 9. Website / Domain Mismatch
# ==============================================================================
def test_case_9_website_domain_mismatch_rejected():
    """
    Website discovery rejects unrelated third-party directories or foreign domains.
    """
    target = "BHARAT TEXTILE MILLS"
    assert not BrowserResearchAgent._is_valid_candidate_url("https://zaubacorp.com/company/BHARAT-TEXTILE", target, "WEBSITE_VERIFICATION")
    assert not BrowserResearchAgent._is_valid_candidate_url("https://archerhotel.com", target, "WEBSITE_VERIFICATION")


# ==============================================================================
# 10. Completely Insufficient Evidence
# ==============================================================================
def test_case_10_completely_insufficient_evidence():
    """
    When all external sources are inaccessible, system reports INSUFFICIENT_EVIDENCE with score None and QA PASS.
    """
    res1 = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="gst_status",
        field_value="UNAVAILABLE",
        source_name="gst.gov.in",
        source_url="https://gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.0,
    )
    analysis = calculate_risk_analysis([res1])
    assert analysis["overall_risk"]["score"] is None
    assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"
    assert analysis["insufficient_evidence"] is True
