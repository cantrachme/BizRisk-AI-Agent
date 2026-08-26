import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.graph.state import ResearchResult
from app.main import app
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.models.risk_signal import RiskSignal
from app.services.evidence import save_research_result
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


@pytest.fixture(name="investigation_id")
def fixture_investigation_id(db_session):
    inv = Investigation(input_data='{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


def make_test_result(result_id="RES-001", **overrides):
    data = {
        "result_id": result_id,
        "task_id": "TASK-001",
        "field_name": "gst_status",
        "field_value": "Active",
        "source_name": "GST Portal",
        "source_url": "https://www.gst.gov.in",
        "retrieved_at": "2026-08-26T10:00:00+00:00",
        "confidence": 0.95,
    }
    data.update(overrides)
    return ResearchResult(**data)


# 1. QA PASS for a clean valid report
def test_qa_pass_clean_report(db_session, investigation_id):
    # Add resolved entity candidate and active GST (no signals)
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    qa_res = validate_report(db_session, investigation_id)
    assert qa_res["status"] == "PASS"
    assert len(qa_res["issues"]) == 0
    assert qa_res["evidence_coverage"] == 1.0
    assert qa_res["score_verified"] is True
    assert qa_res["entity_verified"] is True


# 2. QA FAIL when a major finding has missing evidence_ids
def test_qa_fail_missing_evidence_ids(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    from app.services.report import generate_investigation_report
    original_fn = generate_investigation_report
    def mock_report(*args, **kwargs):
        rep = original_fn(*args, **kwargs)
        rep["major_findings"][0]["evidence_ids"] = []
        return rep

    with patch("app.services.qa.generate_investigation_report", mock_report):
        qa_res = validate_report(db_session, investigation_id)

    assert qa_res["status"] == "FAIL"
    assert any(x["type"] == "MISSING_EVIDENCE" for x in qa_res["issues"])


# 3. QA FAIL when evidence_ids reference non-existent Evidence
def test_qa_fail_invalid_evidence_id(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    from app.services.report import generate_investigation_report
    original_fn = generate_investigation_report
    def mock_report(*args, **kwargs):
        rep = original_fn(*args, **kwargs)
        rep["major_findings"][0]["evidence_ids"] = ["NON_EXISTENT_ID"]
        return rep

    with patch("app.services.qa.generate_investigation_report", mock_report):
        qa_res = validate_report(db_session, investigation_id)

    assert qa_res["status"] == "FAIL"
    assert any(x["type"] == "MISSING_EVIDENCE" for x in qa_res["issues"])


# 4. QA FAIL when resolved entity is missing/invalid
def test_qa_fail_missing_resolved_entity(db_session, investigation_id):
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, res, investigation_id)

    qa_res = validate_report(db_session, investigation_id)
    assert qa_res["status"] == "FAIL"
    assert any(x["type"] == "WRONG_ENTITY" for x in qa_res["issues"])
    assert qa_res["entity_verified"] is False


# 5. QA FAIL when entity confidence is below 0.5
def test_qa_fail_low_entity_confidence(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "different_gstin", "confidence": 0.95, "match_type": "PARTIAL"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    # Missing gstin matching will fall back to scoring, which yields 0.0 confidence (since only business_name is provided, scoring.py ignores it and yields 0.0, below 0.5)
    qa_res = validate_report(db_session, investigation_id)
    assert qa_res["status"] == "FAIL"
    assert any(x["type"] == "WRONG_ENTITY" for x in qa_res["issues"])


# 6. QA FAIL when report risk score does not match deterministic risk analysis
def test_qa_fail_score_mismatch(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    from app.services.report import generate_investigation_report
    original_fn = generate_investigation_report
    def mock_report(*args, **kwargs):
        rep = original_fn(*args, **kwargs)
        rep["overall_risk"]["score"] = 99
        return rep

    with patch("app.services.qa.generate_investigation_report", mock_report):
        qa_res = validate_report(db_session, investigation_id)

    assert qa_res["status"] == "FAIL"
    assert any(x["type"] == "WRONG_RISK_SCORE" for x in qa_res["issues"])
    assert qa_res["score_verified"] is False


# 7. QA FAIL when "fraud" appears without explicit supporting evidence
def test_qa_fail_unsupported_language_indicator_fraud(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    from app.services.report import generate_investigation_report
    original_fn = generate_investigation_report
    def mock_report(*args, **kwargs):
        rep = original_fn(*args, **kwargs)
        rep["major_findings"][0]["description"] = "Proven fraud activity detected."
        return rep

    with patch("app.services.qa.generate_investigation_report", mock_report):
        qa_res = validate_report(db_session, investigation_id)

    assert qa_res["status"] == "FAIL"
    assert any(x["type"] == "REPORT_WORDING" for x in qa_res["issues"])


# 8. QA FAIL when "scam" appears without explicit supporting evidence
def test_qa_fail_unsupported_language_indicator_scam(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    from app.services.report import generate_investigation_report
    original_fn = generate_investigation_report
    def mock_report(*args, **kwargs):
        rep = original_fn(*args, **kwargs)
        rep["major_findings"][0]["description"] = "Entity is associated with a scam."
        return rep

    with patch("app.services.qa.generate_investigation_report", mock_report):
        qa_res = validate_report(db_session, investigation_id)

    assert qa_res["status"] == "FAIL"
    assert any(x["type"] == "REPORT_WORDING" for x in qa_res["issues"])


# 9. QA PASS when a high-risk language indicator is explicitly supported by evidence
def test_qa_pass_supported_language_indicator(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    # Put GST to inactive, but add "scam" keyword to the source name
    res = make_test_result(
        result_id="R1",
        field_name="gst_status",
        field_value="Inactive",
        source_name="GST scam alert registry"
    )
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    from app.services.report import generate_investigation_report
    original_fn = generate_investigation_report
    def mock_report(*args, **kwargs):
        rep = original_fn(*args, **kwargs)
        rep["major_findings"][0]["description"] = "Entity is associated with a scam."
        return rep

    with patch("app.services.qa.generate_investigation_report", mock_report):
        qa_res = validate_report(db_session, investigation_id)

    assert qa_res["status"] == "PASS"


# 10. QA FAIL for a clear contradiction such as inactive GST finding vs active GST evidence
def test_qa_fail_contradiction(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    # Force a GST_INACTIVE major finding in report
    from app.services.report import generate_investigation_report
    original_fn = generate_investigation_report
    def mock_report(*args, **kwargs):
        rep = original_fn(*args, **kwargs)
        rep["major_findings"] = [{
            "code": "GST_INACTIVE",
            "category": "COMPLIANCE",
            "severity": "HIGH",
            "description": "GST inactive finding",
            "evidence_ids": ["R1"],
            "confidence": 0.95,
            "risk_weight": 1.0,
        }]
        return rep

    with patch("app.services.qa.generate_investigation_report", mock_report):
        qa_res = validate_report(db_session, investigation_id)

    assert qa_res["status"] == "FAIL"
    assert any(x["type"] == "UNSUPPORTED_CLAIM" for x in qa_res["issues"])


# 11. QA PASS for valid evidence-backed findings
def test_qa_pass_evidence_backed(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    qa_res = validate_report(db_session, investigation_id)
    assert qa_res["status"] == "PASS"


# 12. QA API returns 200 for valid investigation
def test_qa_api_returns_200(db_session, investigation_id):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    response = client.get(f"/api/v1/investigations/{investigation_id}/qa")
    assert response.status_code == 200

    app.dependency_overrides.clear()


# 13. QA API returns 404 for nonexistent investigation
def test_qa_api_returns_404(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    non_existent = uuid.uuid4()
    response = client.get(f"/api/v1/investigations/{non_existent}/qa")
    assert response.status_code == 404

    app.dependency_overrides.clear()


# 14. QA performs no browser/web/network operations
def test_qa_no_external_calls(db_session, investigation_id):
    with patch("urllib.request.urlopen") as mock_url, patch("socket.socket") as mock_socket:
        validate_report(db_session, investigation_id)
        mock_url.assert_not_called()
        mock_socket.assert_not_called()


# 15. QA is deterministic
def test_qa_is_deterministic(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    qa1 = validate_report(db_session, investigation_id)
    qa2 = validate_report(db_session, investigation_id)
    assert qa1 == qa2


# 16. Repeated QA validation does not create duplicate DB records
def test_qa_repeated_validation_no_duplicates(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    validate_report(db_session, investigation_id)
    validate_report(db_session, investigation_id)
    validate_report(db_session, investigation_id)

    db_signals = db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).all()
    assert len(db_signals) == 1


# 17. Graph qa_node integration works
def test_graph_qa_node_integration(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    initial_state = {
        "investigation_id": str(investigation_id),
        "raw_input": {
            "business_name": "Test Company",
            "gstin": "27ABCDE1234F1Z5",
        },
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [cand, res],
        "planner_loop_count": 0,
        "qa_loop_count": 0,
        "status": "CREATED",
    }

    def mock_execute(self, task):
        return []

    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = graph_app.invoke(initial_state)

    assert "qa_result" in output_state
    assert output_state["qa_result"]["status"] == "PASS"


# 18. QA retry routes back to planner when FAIL and retry count < 2
def test_qa_retry_routes_back_to_planner(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # No resolved entity causes QA FAIL
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    initial_state = {
        "investigation_id": str(investigation_id),
        "raw_input": {
            "business_name": "Test Company",
            "gstin": "27ABCDE1234F1Z5",
        },
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [res],
        "planner_loop_count": 0,
        "qa_loop_count": 0,
        "status": "CREATED",
    }

    def mock_execute(self, task):
        return []

    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = graph_app.invoke(initial_state)

    assert output_state["qa_loop_count"] >= 1


# 19. QA stops at END after retry limit
def test_qa_stops_after_retry_limit(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    initial_state = {
        "investigation_id": str(investigation_id),
        "raw_input": {
            "business_name": "Test Company",
            "gstin": "27ABCDE1234F1Z5",
        },
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [res],
        "planner_loop_count": 0,
        "qa_loop_count": 2,
        "status": "CREATED",
    }

    def mock_execute(self, task):
        return []

    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = graph_app.invoke(initial_state)

    assert output_state["qa_loop_count"] == 3
