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
from app.models.investigation import Investigation
from app.models.investigation_event import InvestigationEvent
from app.services.evidence import save_research_result
from app.services.audit import record_event


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


# 1. Investigation start event is recorded/logged & 2. Node start/completion events are recorded & 3. Investigation completion is recorded & 5. Investigation ID is present
def test_observability_workflow_events_recorded(db_session, investigation_id):
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

    # Check if events table has the chronological events recorded
    events = db_session.query(InvestigationEvent).filter(InvestigationEvent.investigation_id == investigation_id).order_by(InvestigationEvent.created_at.asc()).all()
    assert len(events) > 0
    
    event_types = [e.event_type for e in events]
    assert "INVESTIGATION_STARTED" in event_types
    assert "INVESTIGATION_COMPLETED" in event_types
    assert "NODE_STARTED" in event_types
    assert "NODE_COMPLETED" in event_types
    
    for e in events:
        assert e.investigation_id == investigation_id


# 4. Investigation failure is observable
def test_observability_node_failure_events(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Forcing discovery node to fail by raising an error on DiscoveryAgent.process
    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("app.agents.discovery.DiscoveryAgent.process", side_effect=ValueError("Simulated discovery failure")):
        with pytest.raises(ValueError, match="Simulated discovery failure"):
            graph_app.invoke({
                "investigation_id": str(investigation_id),
                "raw_input": {"business_name": "Fail Company"},
                "normalized_input": {},
                "pending_tasks": [],
                "completed_tasks": [],
                "failed_tasks": [],
                "results": [],
                "planner_loop_count": 0,
                "qa_loop_count": 0,
                "status": "CREATED",
            })

    events = db_session.query(InvestigationEvent).filter(InvestigationEvent.investigation_id == investigation_id).all()
    event_types = [e.event_type for e in events]
    assert "NODE_FAILED" in event_types
    assert "INVESTIGATION_FAILED" in event_types


# 6. QA retry produces an observable event & 7. Retry count is correctly represented
def test_observability_qa_retry_events(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Do not save candidate entities or active results to force a QA FAIL retry cycle
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

    events = db_session.query(InvestigationEvent).filter(InvestigationEvent.investigation_id == investigation_id).all()
    event_types = [e.event_type for e in events]
    assert "QA_RETRY" in event_types
    
    retry_events = [e for e in events if e.event_type == "QA_RETRY"]
    assert len(retry_events) > 0
    # Checks that metadata contains the retry count
    import json
    meta = json.loads(retry_events[0].metadata_json)
    assert "retry_count" in meta
    assert meta["retry_count"] >= 1


# 10. API event history works & 11. Missing investigation returns 404
def test_api_events_endpoint(db_session, investigation_id):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Pre-populate some dummy events
    record_event(db_session, investigation_id, "INVESTIGATION_STARTED", "intake", "STARTED")
    record_event(db_session, investigation_id, "NODE_COMPLETED", "intake", "COMPLETED")

    resp = client.get(f"/api/v1/investigations/{investigation_id}/events")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["event_type"] == "INVESTIGATION_STARTED"
    assert data[1]["event_type"] == "NODE_COMPLETED"

    # Missing ID returns 404
    missing_id = uuid.uuid4()
    resp_missing = client.get(f"/api/v1/investigations/{missing_id}/events")
    assert resp_missing.status_code == 404

    app.dependency_overrides.clear()


# 12. No browser/network calls occur during observability logging
def test_no_network_calls_during_observability_logging(db_session, investigation_id):
    with patch("urllib.request.urlopen") as mock_url, patch("socket.socket") as mock_socket:
        record_event(db_session, investigation_id, "INVESTIGATION_STARTED", "intake", "STARTED")
        mock_url.assert_not_called()
        mock_socket.assert_not_called()
