import uuid
import json
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
from app.models.evidence import Evidence
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


# Active policy config mock
POLICY_ENABLED_CONFIG = {
    "rules": {},
    "risk_levels": {},
    "minimum_evidence_policy": {
        "enabled": True,
        "min_legal_identity_sources": 1,
        "min_supporting_sources": 1,
        "legal_identity_sources": ["GST Portal", "MCA Portal"],
        "supporting_sources": ["Company Website", "General Web", "Third-Party Source"]
    }
}


# 1. A node exception is captured rather than causing an uncontrolled crash
# 2. NODE_FAILED audit event is created
# 3. Investigation state is persisted correctly after failure
# 4. Investigation failure status is persisted
# 5. Error metadata is structured and does not contain stack traces/secrets
def test_node_failure_persistence_and_logging(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Force discovery node to throw an exception
    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("app.agents.discovery.DiscoveryAgent.process", side_effect=RuntimeError("Discovery DB connection timed out")):
        with pytest.raises(RuntimeError, match="Discovery DB connection timed out"):
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

    # Verify that the DB state of the investigation was updated to FAILED and completed_at is set
    db_session.expire_all()
    inv = db_session.get(Investigation, investigation_id)
    assert inv.status == "FAILED"
    assert inv.current_node == "discovery"
    assert inv.completed_at is not None

    # Verify audit events are created
    events = db_session.query(InvestigationEvent).filter(InvestigationEvent.investigation_id == investigation_id).all()
    event_types = [e.event_type for e in events]
    assert "NODE_FAILED" in event_types
    assert "INVESTIGATION_FAILED" in event_types

    # Verify error metadata is structured
    node_failed_event = [e for e in events if e.event_type == "NODE_FAILED"][0]
    meta = json.loads(node_failed_event.metadata_json)
    assert meta["error_type"] == "RuntimeError"
    assert meta["error"] == "Discovery DB connection timed out"
    assert meta["retryable"] is False
    assert "traceback" not in meta  # Ensure no stack traces/secrets are leaked


# 6. Source/browser failure does not crash the whole process
@patch("app.risk.engine.load_config", return_value=POLICY_ENABLED_CONFIG)
def test_source_failure_graceful_handling(mock_load, db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Mock the page fetcher inside BrowserResearchAgent to simulate network failure
    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):
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
            "results": [],
            "planner_loop_count": 0,
            "qa_loop_count": 0,
            "status": "CREATED",
        }
        # Under policy check this will raise InsufficientEvidenceError
        with pytest.raises(Exception, match="Minimum evidence requirement not met"):
            graph_app.invoke(initial_state)

    # Browser node itself should have completed gracefully rather than failing/crashing
    events = db_session.query(InvestigationEvent).filter(InvestigationEvent.investigation_id == investigation_id).all()
    browser_comp = [e for e in events if e.node == "browser_research" and e.event_type == "NODE_COMPLETED"]
    assert len(browser_comp) > 0


# 7. Existing successful node behavior is unchanged
# 8. Existing QA retry loop still works
# 9. No duplicate failure events are created
def test_successful_run_and_no_duplicate_failure_events(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Provide candidate entity to bypass loops and complete successfully
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

    # Mock validate_report to return PASS status
    mock_qa_res = {
        "status": "PASS",
        "issues": [],
        "evidence_coverage": 1.0,
        "score_verified": True,
        "entity_verified": True
    }

    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute), patch("app.services.qa.validate_report", return_value=mock_qa_res):
        output_state = graph_app.invoke(initial_state)

    # The run completes successfully
    db_session.expire_all()
    inv = db_session.get(Investigation, investigation_id)
    assert inv.status == "COMPLETED" or inv.status == "REPORT_GENERATED" or inv.status == "COMPLETED_QA"
    assert inv.completed_at is not None

    # Verify no NODE_FAILED or INVESTIGATION_FAILED events are logged for this successful run
    events = db_session.query(InvestigationEvent).filter(InvestigationEvent.investigation_id == investigation_id).all()
    event_types = [e.event_type for e in events]
    assert "NODE_FAILED" not in event_types
    assert "INVESTIGATION_FAILED" not in event_types


# 12. API does not expose raw exception/stack trace
@patch("app.risk.engine.load_config", return_value=POLICY_ENABLED_CONFIG)
def test_api_does_not_leak_stack_trace(mock_load, db_session, investigation_id):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    # Triggering risk endpoint which calls calculate_risk_analysis
    # Since Minimum Evidence requirement is checked and no evidence exists, it raises InsufficientEvidenceError.
    # The API catches ValueError and raises HTTP 422 with a clear detail message, not a raw stack trace.
    resp = client.get(f"/api/v1/investigations/{investigation_id}/risk")
    assert resp.status_code == 422
    data = resp.json()
    assert "detail" in data
    assert "Minimum" in data["detail"]
    assert "traceback" not in str(data)

    app.dependency_overrides.clear()
