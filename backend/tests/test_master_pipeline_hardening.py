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
from app.services.report import generate_investigation_report
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
# 1. ORION DATA SOLUTIONS 5842 MASTER REGRESSION TEST
# ==============================================================================
def test_orion_data_solutions_5842_pipeline(db_session):
    """
    End-to-end regression test for the exact scenario:
    - Input: business_name: ORION DATA SOLUTIONS 5842, gstin: 27AAACZ58421Z9, location: Mumbai, India
    - External govt sources are blocked/unavailable (CAPTCHA/WAF/503)
    - Search engine returns noisy mix of unrelated links (Archer Hotel, Orion Pharma, coaching)
    - Verifies:
      1. User-supplied GSTIN is strictly preserved in resolved_entity and final report.
      2. Irrelevant candidates (Archer Hotel, Orion Pharma, etc.) are rejected.
      3. Blocked sources are recorded as SOURCE_UNAVAILABLE / BLOCKED, not NOT_FOUND.
      4. Assessment status is INSUFFICIENT_EVIDENCE with overall_risk.score = None.
      5. Investigation reaches COMPLETED with QA PASS.
    """
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    raw_input = {
        "business_name": "ORION DATA SOLUTIONS 5842",
        "gstin": "27AAACZ58421Z9",
        "location": "Mumbai, India",
    }

    inv = Investigation(input_data=json.dumps(raw_input))
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    investigation_id = inv.id

    def mock_fetch_noisy_web(url: str) -> str:
        url_lower = url.lower()
        if "gst.gov.in" in url_lower:
            return "<html><title>Access Denied</title><body>403 Forbidden cloudflare challenge</body></html>"
        if "mca.gov.in" in url_lower or "epfindia.gov.in" in url_lower:
            return "<html><title>503 Service Unavailable</title><body>Service Temporarily Unavailable</body></html>"
        if "duckduckgo.com" in url_lower or "bing.com" in url_lower:
            return """
            <html>
                <title>Search Results</title>
                <body>
                    <a class="result__url" href="https://archerhotel.com/stay">Archer Hotel - Luxury Boutique Hotel</a>
                    <a class="result__url" href="https://orionpharma.in/about">Orion Pharma India - Pharmaceutical Formulations</a>
                    <a class="result__url" href="https://orioncoaching.com/courses">Orion Coaching Classes</a>
                    <a class="result__url" href="https://huel.com/nutrition">Huel Nutrition</a>
                </body>
            </html>
            """
        if "archerhotel.com" in url_lower:
            return "<html><title>Archer Hotel</title><body>Welcome to luxury hotel in Austin. Hotel rooms and suites.</body></html>"
        if "orionpharma.in" in url_lower:
            return "<html><title>Orion Pharma India</title><body>Manufacturer of pharmaceutical formulations and drugs.</body></html>"
        return "<html><title>404 Not Found</title><body>Page not found</body></html>"

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_fetch_noisy_web)):

        state = {
            "investigation_id": str(investigation_id),
            "raw_input": raw_input,
            "normalized_input": {},
            "pending_tasks": [],
            "completed_tasks": [],
            "failed_tasks": [],
            "results": [],
            "planner_loop_count": 0,
            "qa_loop_count": 0,
            "status": "CREATED",
        }

        final_state = graph_app.invoke(state)

    db_session.expire_all()
    updated_inv = db_session.get(Investigation, investigation_id)

    # 1. Investigation completes
    assert updated_inv.status == "COMPLETED"

    # 2. Risk Score is None (INSUFFICIENT_EVIDENCE, no fake risk score)
    assert updated_inv.risk_score is None

    # 3. Report checks
    report = final_state.get("report") or {}
    entity = report.get("entity") or {}

    # User-supplied GSTIN is strictly preserved
    assert entity.get("gstin") == "27AAACZ58421Z9"
    assert entity.get("business_name") == "ORION DATA SOLUTIONS 5842"
    assert "Mumbai" in str(entity.get("location"))

    # Entity confidence is recorded
    assert final_state.get("entity_confidence", 0.0) > 0.0

    # Risk level is INSUFFICIENT_EVIDENCE
    assert report.get("overall_risk", {}).get("level") == "INSUFFICIENT_EVIDENCE"
    assert report.get("overall_risk", {}).get("score") is None
    assert report.get("assessment_status") == "INSUFFICIENT_EVIDENCE"

    # Structured reason codes and source limitations are exposed
    assert "AUTHORITATIVE_SOURCES_UNAVAILABLE" in report.get("reason_codes", [])
    assert len(report.get("source_limitations", [])) > 0

    # 4. QA passes
    qa_res = final_state.get("qa_result") or {}
    assert qa_res.get("status") == "PASS"


# ==============================================================================
# 2. TEST A: Supplied GSTIN Preservation
# ==============================================================================
def test_a_supplied_gstin_preservation():
    raw = {
        "business_name": "Apex Data Systems",
        "gstin": "27ABCDE1234F1Z5",
        "location": "Pune, India",
    }
    norm = IntakeAgent().process(raw)
    assert norm["gstin"] == "27ABCDE1234F1Z5"
    assert norm["identifier_provenance"]["gstin"] == "USER_SUPPLIED"

    # Simulated graph state preserves user GSTIN in resolved entity
    from app.graph.nodes import entity_resolution_node
    state = {
        "investigation_id": "00000000-0000-0000-0000-000000000001",
        "normalized_input": norm,
        "results": [],
        "status": "RESEARCH_COMPLETED",
    }
    res_state = entity_resolution_node(state)
    assert res_state["resolved_entity"]["gstin"] == "27ABCDE1234F1Z5"


# ==============================================================================
# 3. TEST B: Unrelated Orion Company (Orion Pharma rejected)
# ==============================================================================
def test_b_unrelated_orion_pharma_rejected():
    target = {
        "business_name": "ORION DATA SOLUTIONS",
        "location": "Mumbai",
    }
    candidate = {
        "business_name": "Orion Pharma India Private Limited",
        "location": "Mumbai",
    }
    score = score_entities(target, candidate)
    assert score == 0.0, "Orion Pharma must score 0.0 against Orion Data Solutions due to sector incompatibility"


# ==============================================================================
# 4. TEST C: Unrelated Generic Result (Archer Hotel rejected)
# ==============================================================================
def test_c_unrelated_archer_hotel_rejected():
    target = {
        "business_name": "ORION DATA SOLUTIONS",
        "location": "Mumbai",
    }
    candidate = {
        "business_name": "Archer Hotel Austin",
        "location": "Austin",
    }
    score = score_entities(target, candidate)
    assert score == 0.0, "Archer Hotel must score 0.0 against Orion Data Solutions"


# ==============================================================================
# 5. TEST D: Blocked Source produces SOURCE_UNAVAILABLE, not NOT_FOUND
# ==============================================================================
def test_d_blocked_source_relevance_and_status():
    blocked_html = "<html><title>Access Denied</title><body>403 Forbidden cloudflare security check.</body></html>"
    classification = BrowserResearchAgent._is_failed_or_blocked_retrieval(blocked_html, "ORION DATA SOLUTIONS")
    assert classification == "BLOCKED_OR_ERROR"
    assert classification != "NO_RESULTS"


# ==============================================================================
# 6. TEST E: Search Engine Snippet is Never Evidence
# ==============================================================================
def test_e_search_engine_url_not_evidence():
    task = ResearchTask(
        task_id="TASK-001",
        task_type="WEBSITE_VERIFICATION",
        target="ORION DATA SOLUTIONS",
        objective="Verify website",
        required_fields=["legal_name"],
        priority=1,
    )
    page_data = {
        "title": "Search Results for ORION DATA SOLUTIONS",
        "text": "Search results for ORION DATA SOLUTIONS",
        "url": "https://duckduckgo.com/?q=ORION%20DATA%20SOLUTIONS",
    }
    val, basis = BrowserResearchAgent._extract_field_value_with_basis(task, "legal_name", page_data)
    assert val == "NOT_FOUND"


# ==============================================================================
# 7. TEST F: No Valid Sources -> INSUFFICIENT_EVIDENCE with score None
# ==============================================================================
def test_f_no_valid_sources_yields_insufficient_evidence():
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


# ==============================================================================
# 8. TEST G: Exact Authoritative Identifier Match -> High Entity Confidence
# ==============================================================================
def test_g_exact_authoritative_match_high_confidence():
    target = {
        "business_name": "TCS LIMITED",
        "gstin": "27AAACT2727Q1ZW",
        "location": "Mumbai",
    }
    candidate = {
        "business_name": "Tata Consultancy Services Limited",
        "gstin": "27AAACT2727Q1ZW",
        "location": "Mumbai",
    }
    res = resolve_entity(target, [candidate])
    assert res["matched"] is True
    assert res["confidence"] >= 0.90
    assert res["match_type"] == "EXACT"


# ==============================================================================
# 9. TEST H: Conflicting Identifier Flags Conflict
# ==============================================================================
def test_h_conflicting_identifier_flags_conflict():
    target = {
        "business_name": "ABC Tech Solutions",
        "gstin": "27ABCDE1234F1Z5",
    }
    candidate = {
        "business_name": "ABC Tech Solutions",
        "gstin": "29XYZAB9876C1Z0",  # Different state & entity GSTIN
    }
    score = score_entities(target, candidate)
    assert score == 0.0, "Conflicting GSTINs must score 0.0"


# ==============================================================================
# 10. TEST I: Official Website Requires Multi-Signal Identity Match
# ==============================================================================
def test_i_official_website_candidate_url_validation():
    target = "ORION DATA SOLUTIONS 5842"
    # Unrelated domains rejected pre-navigation
    assert not BrowserResearchAgent._is_valid_candidate_url("https://archerhotel.com/room", target, "WEBSITE_VERIFICATION")
    assert not BrowserResearchAgent._is_valid_candidate_url("https://orionpharma.in/contact", target, "WEBSITE_VERIFICATION")
    assert not BrowserResearchAgent._is_valid_candidate_url("https://zaubacorp.com/company/ORION-DATA", target, "WEBSITE_VERIFICATION")
    # Domain containing distinctive target tokens accepted
    assert BrowserResearchAgent._is_valid_candidate_url("https://oriondatasolutions.in", target, "WEBSITE_VERIFICATION")


# ==============================================================================
# 11. TEST J: Technical Failure Never Becomes Negative Business Evidence
# ==============================================================================
def test_j_technical_failure_not_negative_evidence():
    # A 503 error on MCA or GST does not trigger GST_INACTIVE or COMPANY_STRUCK_OFF risk signals
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
    res2 = ResearchResult(
        result_id="RES-002",
        task_id="TASK-002",
        field_name="company_status",
        field_value="UNAVAILABLE",
        source_name="mca.gov.in",
        source_url="https://mca.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.0,
    )
    analysis = calculate_risk_analysis([res1, res2])
    # No risk signals triggered
    assert len(analysis["risk_signals"]) == 0
    assert analysis["overall_risk"]["score"] is None
