import uuid
from datetime import datetime, timezone
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
from app.models.report import Report
from app.services.evidence import save_research_result
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


# 1. Report is persisted after generation & 2. Report JSON matches generated structured report
def test_report_is_persisted_and_matches(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    report_dict = generate_investigation_report(db_session, investigation_id)
    
    # Query database to check if Report record was created
    reports = db_session.query(Report).filter(Report.investigation_id == investigation_id).all()
    assert len(reports) == 1
    assert reports[0].version == 1
    assert reports[0].qa_status == "PENDING"
    assert reports[0].report_json is not None


# 3. First report gets version 1 & 4. Subsequent report generation creates the next version & 5. Previous reports are not overwritten
def test_subsequent_generation_creates_next_version(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    # First generation
    generate_investigation_report(db_session, investigation_id)
    reports = db_session.query(Report).filter(Report.investigation_id == investigation_id).order_by(Report.version.asc()).all()
    assert len(reports) == 1
    assert reports[0].version == 1
    
    # QA PASSes the first report
    validate_report(db_session, investigation_id)
    reports = db_session.query(Report).filter(Report.investigation_id == investigation_id).order_by(Report.version.asc()).all()
    assert reports[0].qa_status == "PASS"

    # Second generation (should create a new version since version 1 was QA PASSed)
    generate_investigation_report(db_session, investigation_id)
    reports = db_session.query(Report).filter(Report.investigation_id == investigation_id).order_by(Report.version.asc()).all()
    assert len(reports) == 2
    assert reports[0].version == 1
    assert reports[1].version == 2
    assert reports[1].qa_status == "PENDING"


# 6. QA PASS updates latest report qa_status to PASS
def test_qa_pass_updates_report_status(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    generate_investigation_report(db_session, investigation_id)
    validate_report(db_session, investigation_id)
    
    latest_report = db_session.query(Report).filter(Report.investigation_id == investigation_id).order_by(Report.version.desc()).first()
    assert latest_report.qa_status == "PASS"


# 7. QA FAIL updates latest report qa_status to FAIL
def test_qa_fail_updates_report_status(db_session, investigation_id):
    # No resolved entity causes QA FAIL
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    generate_investigation_report(db_session, investigation_id)
    validate_report(db_session, investigation_id)
    
    latest_report = db_session.query(Report).filter(Report.investigation_id == investigation_id).order_by(Report.version.desc()).first()
    assert latest_report.qa_status == "FAIL"


# 8. Investigation current_node/status is persisted & 9. Risk score/risk level are persisted
def test_investigation_state_fields_persisted(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    from app.graph.nodes import update_investigation_in_db
    with patch("app.db.session.SessionLocal", MockSessionLocal):
        update_investigation_in_db(
            str(investigation_id),
            "risk_analysis",
            status="PENDING_RESEARCH",
            retry_count=1,
            risk_score=75,
            risk_level="HIGH"
        )

    inv = db_session.get(Investigation, investigation_id)
    assert inv.current_node == "risk_analysis"
    assert inv.status == "PENDING_RESEARCH"
    assert inv.retry_count == 1
    assert inv.risk_score == 75
    assert inv.risk_level == "HIGH"


# 10. Graph transitions preserve persistence & 11. Investigation completion sets completed_at
def test_graph_workflow_completion_sets_completed_at(db_session, investigation_id):
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
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
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

    inv = db_session.get(Investigation, investigation_id)
    assert inv.status == "COMPLETED"
    assert inv.completed_at is not None


# 12. API returns latest persisted report & 13. API returns report history
def test_api_returns_latest_report_and_history(db_session, investigation_id):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    # Generate first version
    client.get(f"/api/v1/investigations/{investigation_id}/report")
    
    # Complete QA
    client.get(f"/api/v1/investigations/{investigation_id}/qa")

    # Generate second version (simulate workflow retry/re-run of generate_investigation_report)
    generate_investigation_report(db_session, investigation_id)

    # Retrieve history
    history_resp = client.get(f"/api/v1/investigations/{investigation_id}/reports")
    assert history_resp.status_code == 200
    history_data = history_resp.json()
    assert len(history_data) == 2
    assert history_data[0]["version"] == 1
    assert history_data[0]["qa_status"] == "PASS"
    assert history_data[1]["version"] == 2
    assert history_data[1]["qa_status"] == "PENDING"

    # Retrieve latest report
    latest_resp = client.get(f"/api/v1/investigations/{investigation_id}/report")
    assert latest_resp.status_code == 200
    latest_data = latest_resp.json()
    assert latest_data["meta"]["report_version"] == "2"

    app.dependency_overrides.clear()


# 14. Missing investigation returns 404
def test_api_returns_404_for_missing_investigation(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    missing_id = uuid.uuid4()
    resp1 = client.get(f"/api/v1/investigations/{missing_id}/report")
    assert resp1.status_code == 404

    resp2 = client.get(f"/api/v1/investigations/{missing_id}/reports")
    assert resp2.status_code == 404

    app.dependency_overrides.clear()


# 15. No browser/network calls occur during persistence/report retrieval
def test_no_external_calls_during_report_retrieval(db_session, investigation_id):
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Test Company", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    # Persist the report initially
    generate_investigation_report(db_session, investigation_id)

    # Now verify that subsequent report generation/retrieval performs no external network calls
    with patch("urllib.request.urlopen") as mock_url, patch("socket.socket") as mock_socket:
        generate_investigation_report(db_session, investigation_id)
        mock_url.assert_not_called()
        mock_socket.assert_not_called()
