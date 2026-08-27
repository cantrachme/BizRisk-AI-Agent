import pytest
import uuid
import json
from unittest import mock
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastapi.testclient import TestClient

from app.db.base import Base
from app.main import app as fastapi_app
from app.graph.state import InvestigationState, ResearchTask as GraphTask, ResearchResult
from app.graph.nodes import (
    intake_node,
    discovery_node,
    planner_node,
    browser_node,
    entity_resolution_node,
    risk_analysis_node,
    report_generation_node,
    qa_node
)
from app.models.investigation import Investigation
from app.models.research_task import ResearchTask as ResearchTaskModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.investigation_event import InvestigationEvent
from app.services.investigation import (
    serialize_state,
    deserialize_state,
    recover_investigation_state,
)


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


@pytest.fixture(name="client")
def fixture_client(db_session):
    from app.db import get_db
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    fastapi_app.dependency_overrides[get_db] = override_get_db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(name="investigation_id")
def fixture_investigation_id(db_session):
    inv = Investigation(
        input_data='{"gstin": "09ABCDE1234F1Z5", "user_id": "user-123"}',
        user_id="user-123",
        raw_input='{"gstin": "09ABCDE1234F1Z5", "user_id": "user-123"}',
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


def test_model_columns(db_session, investigation_id):
    inv = db_session.get(Investigation, investigation_id)
    assert inv.user_id == "user-123"
    assert "user_id" in inv.raw_input
    assert hasattr(inv, "normalized_input")
    assert hasattr(inv, "current_graph_node")
    assert hasattr(inv, "completed_timestamp")
    assert hasattr(inv, "persistent_graph_state")


def test_serialization():
    task = GraphTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="09ABCDE1234F1Z5",
        objective="Verify GST",
        required_fields=["legal_name"],
        priority=1,
    )
    res = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="legal_name",
        field_value="ACME Corp",
        source_name="GST Portal",
        source_url="https://www.gst.gov.in",
        retrieved_at="2026-08-28T00:00:00Z",
        confidence=0.95,
    )
    state = {
        "pending_tasks": [task],
        "results": [res],
        "status": "PENDING_RESEARCH",
    }
    
    serialized = serialize_state(state)
    assert "TASK-001" in serialized
    assert "ACME Corp" in serialized
    
    deserialized = deserialize_state(serialized)
    assert len(deserialized["pending_tasks"]) == 1
    assert isinstance(deserialized["pending_tasks"][0], GraphTask)
    assert deserialized["pending_tasks"][0].task_id == "TASK-001"
    assert deserialized["pending_tasks"][0].priority == 1
    
    assert len(deserialized["results"]) == 1
    assert isinstance(deserialized["results"][0], ResearchResult)
    assert deserialized["results"][0].result_id == "RES-001"
    assert deserialized["results"][0].confidence == 0.95


def test_state_persistence_across_nodes(db_session, investigation_id):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "raw_input": {"gstin": "09ABCDE1234F1Z5", "user_id": "user-123"},
        "normalized_input": {"gstin": "09ABCDE1234F1Z5"},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "created",
        "research_depth": 0,
        "browser_actions": 0,
        "browser_tasks_count": 0,
        "llm_calls": 0,
        "token_usage": 0,
        "stop_reason": None,
    }

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
        # 1. Intake
        state_update_1 = intake_node(state)
        state.update(state_update_1)
        inv = db_session.get(Investigation, investigation_id)
        assert inv.status == "NORMALIZED"
        assert inv.current_graph_node == "intake"
        assert inv.persistent_graph_state is not None
        assert "normalized_input" in json.loads(inv.persistent_graph_state)

        # 2. Discovery
        # Mock DiscoveryAgent
        with mock.patch("app.agents.discovery.DiscoveryAgent.process", return_value={"candidate_entities": []}):
            state_update_2 = discovery_node(state)
        state.update(state_update_2)
        db_session.refresh(inv)
        assert inv.status == "DISCOVERY_COMPLETED"
        assert inv.current_graph_node == "discovery"

        # 3. Planner
        with mock.patch("app.agents.planner.PlannerAgent.plan", return_value=[]):
            state_update_3 = planner_node(state)
        state.update(state_update_3)
        db_session.refresh(inv)
        assert inv.status == "COMPLETED"
        assert inv.current_graph_node == "planner"


def test_list_incomplete_investigations(client, db_session, investigation_id):
    # Retrieve incomplete investigations list
    resp = client.get("/api/v1/investigations/incomplete")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(investigation_id)
    assert data[0]["status"] == "created"


def test_crash_recovery_resumption(client, db_session, investigation_id):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Reconstruct state from pre-crash state
    task = GraphTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="09ABCDE1234F1Z5",
        objective="Verify GST",
        required_fields=["legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    serialized_state = serialize_state({
        "investigation_id": str(investigation_id),
        "raw_input": {"gstin": "09ABCDE1234F1Z5", "user_id": "user-123"},
        "normalized_input": {"gstin": "09ABCDE1234F1Z5"},
        "pending_tasks": [task],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "PENDING_RESEARCH",
        "research_depth": 0,
        "browser_actions": 0,
        "browser_tasks_count": 0,
        "llm_calls": 0,
        "token_usage": 0,
        "stop_reason": None,
    })

    inv = db_session.get(Investigation, investigation_id)
    inv.persistent_graph_state = serialized_state
    inv.status = "PENDING_RESEARCH"
    inv.current_graph_node = "planner"
    db_session.commit()

    # Pre-populate research tasks in DB to simulate persistence
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GST",
            status="PENDING",
        )
    )
    db_session.commit()

    # Mock browser and QA validation
    from app.agents.browser import BrowserResearchAgent
    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><title>GST Portal</title><body>Active GST.</body></html>")

    try:
        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            res_resp = client.post(f"/api/v1/investigations/{investigation_id}/resume")
        assert res_resp.status_code == 200
        res_data = res_resp.json()
        assert res_data["status"] == "COMPLETED"

        # Verify DB states
        db_session.refresh(inv)
        assert inv.status == "COMPLETED"
        assert inv.completed_timestamp is not None
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


def test_backward_compatibility_recovery(db_session, investigation_id):
    # Set up DB without persistent_graph_state (missing/null state)
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GST",
            status="COMPLETED",
        )
    )
    db_session.add(
        EvidenceModel(
            investigation_id=investigation_id,
            research_result_id="RES-001",
            task_id="TASK-001",
            field_name="legal_name",
            field_value="ACME Corp",
            source_name="GST Portal",
            source_url="https://www.gst.gov.in",
            confidence=0.95,
            retrieved_timestamp=datetime.now(timezone.utc),
        )
    )
    db_session.commit()

    # Recover state
    state = recover_investigation_state(db_session, investigation_id)
    assert state is not None
    assert len(state["completed_tasks"]) == 1
    assert state["completed_tasks"][0].task_id == "TASK-001"
    assert len(state["results"]) == 1
    assert state["results"][0].field_name == "legal_name"
    assert state["results"][0].field_value == "ACME Corp"
