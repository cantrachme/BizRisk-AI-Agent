import uuid
from datetime import timezone
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
from app.services.risk_analysis import analyze_investigation


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


# 1. Test Persisted Evidence is loaded and analyzed
def test_persisted_evidence_loaded_and_analyzed(db_session, investigation_id):
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    analysis = analyze_investigation(db_session, investigation_id)
    assert analysis["overall_risk"]["score"] == 30
    assert len(analysis["risk_signals"]) == 1
    assert analysis["risk_signals"][0]["code"] == "GST_INACTIVE"


# 2. Test conflicting evidence generates the expected risk signal
def test_conflicting_evidence_generates_signal(db_session, investigation_id):
    res1 = make_test_result(result_id="R1", field_name="legal_name", field_value="Acme Corp")
    res2 = make_test_result(result_id="R2", field_name="legal_name", field_value="Apex Corp")
    save_research_result(db_session, res1, investigation_id)
    save_research_result(db_session, res2, investigation_id)

    analysis = analyze_investigation(db_session, investigation_id)
    assert len(analysis["risk_signals"]) == 1
    assert analysis["risk_signals"][0]["code"] == "LEGAL_NAME_CONFLICT"


# 3. Test non-conflicting evidence does not generate a false conflict signal
def test_non_conflicting_evidence_no_signal(db_session, investigation_id):
    res1 = make_test_result(result_id="R1", field_name="legal_name", field_value="Acme Corp Private Limited")
    res2 = make_test_result(result_id="R2", field_name="legal_name", field_value="acme corp pvt ltd")
    save_research_result(db_session, res1, investigation_id)
    save_research_result(db_session, res2, investigation_id)

    analysis = analyze_investigation(db_session, investigation_id)
    # The normalization logic should treat these as equivalent, so no signal
    assert len(analysis["risk_signals"]) == 0


# 4. Test every generated signal references valid persisted Evidence IDs
def test_signal_references_valid_evidence_ids(db_session, investigation_id):
    res1 = make_test_result(result_id="R1", field_name="legal_name", field_value="Acme Corp")
    res2 = make_test_result(result_id="R2", field_name="legal_name", field_value="Apex Corp")
    save_research_result(db_session, res1, investigation_id)
    save_research_result(db_session, res2, investigation_id)

    analysis = analyze_investigation(db_session, investigation_id)
    sig = analysis["risk_signals"][0]
    assert set(sig["evidence_ids"]) == {"R1", "R2"}


# 5. Test signal generation is deterministic
def test_analysis_is_deterministic(db_session, investigation_id):
    res1 = make_test_result(result_id="R1", field_name="legal_name", field_value="Acme Corp")
    res2 = make_test_result(result_id="R2", field_name="legal_name", field_value="Apex Corp")
    save_research_result(db_session, res1, investigation_id)
    save_research_result(db_session, res2, investigation_id)

    analysis1 = analyze_investigation(db_session, investigation_id)
    analysis2 = analyze_investigation(db_session, investigation_id)

    assert analysis1["overall_risk"] == analysis2["overall_risk"]
    assert analysis1["category_scores"] == analysis2["category_scores"]
    assert len(analysis1["risk_signals"]) == len(analysis2["risk_signals"])
    assert analysis1["risk_signals"][0]["code"] == analysis2["risk_signals"][0]["code"]


# 6. Test running multiple times does not create duplicate signals
def test_no_duplicate_signals_on_multiple_runs(db_session, investigation_id):
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    # Run analysis multiple times
    analyze_investigation(db_session, investigation_id)
    analyze_investigation(db_session, investigation_id)
    analyze_investigation(db_session, investigation_id)

    # Check total signals in database
    db_signals = db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).all()
    assert len(db_signals) == 1
    assert db_signals[0].code == "GST_INACTIVE"


# 7. Test empty evidence is handled correctly
def test_empty_evidence_handling(db_session, investigation_id):
    # Setup some pre-existing signals first
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)
    analyze_investigation(db_session, investigation_id)
    
    # Assert signals exist
    assert db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).count() == 1

    # Remove all evidence
    db_session.query(Evidence).filter(Evidence.investigation_id == investigation_id).delete()
    db_session.commit()

    # Re-run analysis with empty evidence
    analysis = analyze_investigation(db_session, investigation_id)
    assert analysis["overall_risk"]["score"] == 0
    assert len(analysis["risk_signals"]) == 0

    # Stale signals should be cleared from database
    assert db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).count() == 0


# 8. Test no browser or external network operations occur
def test_no_external_operations(db_session, investigation_id):
    # Simply running analysis with standard python urllib / socket mocked
    with patch("urllib.request.urlopen") as mock_url, patch("socket.socket") as mock_socket:
        analyze_investigation(db_session, investigation_id)
        mock_url.assert_not_called()
        mock_socket.assert_not_called()


# 9. Test API Integration works
def test_api_integration(db_session, investigation_id):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

    client = TestClient(app)
    response = client.get(f"/api/v1/investigations/{investigation_id}/risk")

    assert response.status_code == 200
    data = response.json()
    assert data["overall_risk"]["score"] == 30
    assert len(data["risk_signals"]) == 1
    assert data["risk_signals"][0]["code"] == "GST_INACTIVE"

    app.dependency_overrides.clear()


# 10. Test Graph risk_analysis_node integration works
def test_graph_node_integration(db_session, investigation_id):
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

    assert "overall_risk" in output_state
    assert output_state["overall_risk"]["score"] == 30

    db_signals = db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).all()
    assert len(db_signals) == 1
    assert db_signals[0].code == "GST_INACTIVE"
