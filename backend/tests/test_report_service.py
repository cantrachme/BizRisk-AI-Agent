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
from app.services.report import generate_investigation_report


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
    inv = Investigation(input_data='{"business_name": "Test Company"}')
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


# 1. Generate report for an existing investigation
def test_generate_report_for_existing_investigation(db_session, investigation_id):
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    report = generate_investigation_report(db_session, investigation_id)

    assert report is not None
    assert report["overall_risk"]["score"] == 30
    assert len(report["major_findings"]) == 1
    assert report["major_findings"][0]["code"] == "GST_INACTIVE"


# 2. 404 API behavior for nonexistent investigation
def test_report_api_404_not_found(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    non_existent = uuid.uuid4()
    response = client.get(f"/api/v1/investigations/{non_existent}/report")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

    app.dependency_overrides.clear()


# 3. Empty evidence report
def test_empty_evidence_report(db_session, investigation_id):
    report = generate_investigation_report(db_session, investigation_id)

    assert report["overall_risk"]["score"] == 0
    assert len(report["major_findings"]) == 0
    assert len(report["evidence_summary"]) == 0


# 4. Report contains deterministic risk analysis
def test_report_contains_deterministic_risk_analysis(db_session, investigation_id):
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    report = generate_investigation_report(db_session, investigation_id)

    assert "overall_risk" in report
    assert report["overall_risk"]["score"] == 30
    assert report["overall_risk"]["level"] == "LOW"


# 5. Risk signals are included
def test_risk_signals_included(db_session, investigation_id):
    res1 = make_test_result(result_id="R1", field_name="legal_name", field_value="Company A")
    res2 = make_test_result(result_id="R2", field_name="legal_name", field_value="Company B")
    save_research_result(db_session, res1, investigation_id)
    save_research_result(db_session, res2, investigation_id)

    report = generate_investigation_report(db_session, investigation_id)

    assert len(report["major_findings"]) == 1
    assert report["major_findings"][0]["code"] == "LEGAL_NAME_CONFLICT"


# 6. Every material finding references valid persisted Evidence IDs
def test_findings_reference_valid_evidence_ids(db_session, investigation_id):
    res1 = make_test_result(result_id="R1", field_name="legal_name", field_value="Company A")
    res2 = make_test_result(result_id="R2", field_name="legal_name", field_value="Company B")
    save_research_result(db_session, res1, investigation_id)
    save_research_result(db_session, res2, investigation_id)

    report = generate_investigation_report(db_session, investigation_id)

    findings = report["major_findings"]
    evidence_ids = findings[0]["evidence_ids"]
    assert set(evidence_ids) == {"R1", "R2"}

    # Ensure they exist in DB
    for ev_id in evidence_ids:
        db_ev = db_session.query(Evidence).filter(
            Evidence.investigation_id == investigation_id,
            Evidence.research_result_id == ev_id
        ).first()
        assert db_ev is not None


# 7. Evidence source metadata is included
def test_evidence_source_metadata_included(db_session, investigation_id):
    res = make_test_result(
        result_id="R1",
        field_name="gst_status",
        field_value="Inactive",
        source_name="GST Registry",
        source_url="https://gst.example.com",
    )
    save_research_result(db_session, res, investigation_id)

    report = generate_investigation_report(db_session, investigation_id)

    summary = report["evidence_summary"]
    assert len(summary) == 1
    assert summary[0]["evidence_id"] == "R1"
    assert summary[0]["source_name"] == "GST Registry"
    assert summary[0]["source_url"] == "https://gst.example.com"
    assert "retrieved_at" in summary[0]
    assert summary[0]["confidence"] == 0.95


# 8. No browser/network calls occur
def test_no_external_network_or_browser_calls(db_session, investigation_id):
    with patch("urllib.request.urlopen") as mock_url, patch("socket.socket") as mock_socket:
        generate_investigation_report(db_session, investigation_id)
        mock_url.assert_not_called()
        mock_socket.assert_not_called()


# 9. Report generation is deterministic
def test_report_generation_is_deterministic(db_session, investigation_id):
    res1 = make_test_result(result_id="R1", field_name="legal_name", field_value="Company A")
    res2 = make_test_result(result_id="R2", field_name="legal_name", field_value="Company B")
    save_research_result(db_session, res1, investigation_id)
    save_research_result(db_session, res2, investigation_id)

    report1 = generate_investigation_report(db_session, investigation_id)
    report2 = generate_investigation_report(db_session, investigation_id)

    # Discard dynamic generated_at timestamp for comparison
    report1["meta"].pop("generated_at", None)
    report2["meta"].pop("generated_at", None)

    assert report1 == report2


# 10. Repeated report generation does not create duplicate database records
def test_repeated_runs_do_not_duplicate_records(db_session, investigation_id):
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    # Run multiple times
    generate_investigation_report(db_session, investigation_id)
    generate_investigation_report(db_session, investigation_id)
    generate_investigation_report(db_session, investigation_id)

    # Verify no duplicate risk signals exist in database
    db_signals = db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).all()
    assert len(db_signals) == 1


# 11. API endpoint integration
def test_api_report_endpoint(db_session, investigation_id):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    client = TestClient(app)
    response = client.get(f"/api/v1/investigations/{investigation_id}/report")

    assert response.status_code == 200
    data = response.json()
    assert data["overall_risk"]["score"] == 30
    assert len(data["major_findings"]) == 1
    assert data["major_findings"][0]["code"] == "GST_INACTIVE"

    app.dependency_overrides.clear()


# 12. Graph/report-node integration
def test_graph_node_report_integration(db_session, investigation_id):
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
            "business_name": "ABC Foods Pvt Ltd",
            "gstin": "27abcde1234f1z5",
            "website": "abcfoods.in",
            "location": "Noida",
        },
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [res],
        "planner_loop_count": 0,
        "status": "CREATED",
    }

    def mock_execute(self, task):
        return []

    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = graph_app.invoke(initial_state)

    assert "report" in output_state
    report = output_state["report"]
    assert report is not None
    assert report["overall_risk"]["score"] == 30
    assert len(report["major_findings"]) == 1
    assert report["major_findings"][0]["code"] == "GST_INACTIVE"
