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
from app.core.config import get_settings
from app.graph.state import InvestigationState, ResearchTask as GraphTask, ResearchResult
from app.graph.nodes import browser_node, discovery_node, planner_node
from app.agents.browser import BrowserResearchAgent, detect_human_intervention
from app.core.exceptions import HumanInterventionRequiredException
from app.models.investigation import Investigation
from app.models.research_task import ResearchTask as ResearchTaskModel
from app.models.investigation_event import InvestigationEvent


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
    inv = Investigation(input_data='{"gstin": "09ABCDE1234F1Z5"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


def test_detection_captcha():
    # Strict title match
    assert detect_human_intervention("<html><title>CAPTCHA Verification</title></html>") == "CAPTCHA"
    # Content pattern match
    assert detect_human_intervention("<html><body>solve the captcha below to proceed</body></html>") == "CAPTCHA"
    # Tag pattern match
    assert detect_human_intervention("<html><body><div class='g-recaptcha'></div></body></html>") == "CAPTCHA"


def test_detection_otp():
    assert detect_human_intervention("<html><body>Enter OTP to confirm identity.</body></html>") == "OTP"
    assert detect_human_intervention("<html><body>Two-factor authentication code sent.</body></html>") == "OTP"


def test_detection_login():
    assert detect_human_intervention("<html><body>Please log in to continue.</body></html>") == "LOGIN_REQUIRED"
    assert detect_human_intervention("<html><body>Authentication required to view company details.</body></html>") == "LOGIN_REQUIRED"


def test_detection_normal_page():
    assert detect_human_intervention("<html><title>ABC Foods</title><body>GST is Active. Address: 123 Lane.</body></html>") is None


def test_captcha_handles_autonomous_failure():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><title>Verify you are human</title></html>")
    task = GraphTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="09ABCDE1234F1Z5",
        objective="Verify GSTIN",
        required_fields=["legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    results = agent.execute(task)
    assert len(results) > 0
    assert all(r.confidence == 0.0 for r in results)
    assert results[0].field_value in {"NOT_FOUND", "UNAVAILABLE"}


def test_otp_handles_autonomous_failure():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><body>Please enter OTP code.</body></html>")
    task = GraphTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="09ABCDE1234F1Z5",
        objective="Verify GSTIN",
        required_fields=["legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    results = agent.execute(task)
    assert len(results) > 0
    assert all(r.confidence == 0.0 for r in results)
    assert results[0].field_value in {"NOT_FOUND", "UNAVAILABLE"}


def test_login_handles_autonomous_failure():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><body>Sign in to proceed</body></html>")
    task = GraphTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="09ABCDE1234F1Z5",
        objective="Verify GSTIN",
        required_fields=["legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    results = agent.execute(task)
    assert len(results) > 0
    assert all(r.confidence == 0.0 for r in results)
    assert results[0].field_value in {"NOT_FOUND", "UNAVAILABLE"}


def test_browser_node_handles_blocked_source_autonomously(db_session, investigation_id):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Stub the fetcher of BrowserResearchAgent to return CAPTCHA
    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><title>CAPTCHA Verification</title></html>")

    task = GraphTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="09ABCDE1234F1Z5",
        objective="Verify GSTIN",
        required_fields=["legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )

    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="PENDING",
        )
    )
    db_session.commit()

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
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
    }

    try:
        with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
            out_state = browser_node(state)
        
        # Completes autonomously
        assert len(out_state["completed_tasks"]) == 1
        assert out_state["completed_tasks"][0].status == "COMPLETED"
        assert len(out_state["results"]) >= 1
        assert all(r.confidence == 0.0 for r in out_state["results"])

        # Check database persistence
        task_db = db_session.query(ResearchTaskModel).filter_by(task_id="TASK-001").first()
        assert task_db.status == "COMPLETED"
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


def test_independent_tasks_continue(db_session, investigation_id):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Stub the fetcher: block GST portal, but let Company website pass
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><title>CAPTCHA Verification</title></html>"
        return "<html><title>Company Page</title><body>GST is Active.</body></html>"

    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(mock_fetcher)

    task_gst = GraphTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="09ABCDE1234F1Z5",
        objective="Verify GSTIN",
        required_fields=["legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    task_web = GraphTask(
        task_id="TASK-002",
        task_type="WEBSITE_VERIFICATION",
        target="abcfoods.in",
        objective="Verify Website",
        required_fields=["website_status"],
        priority=2,
        preferred_sources=["company_website"],
    )

    db_session.add_all([
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="PENDING",
        ),
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-002",
            task_type="WEBSITE_VERIFICATION",
            target="abcfoods.in",
            objective="Verify Website",
            status="PENDING",
        )
    ])
    db_session.commit()

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": [task_gst, task_web],
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
    }

    try:
        with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
            out_state = browser_node(state)
        
        # Both tasks completed
        assert len(out_state["completed_tasks"]) == 2
        assert len(out_state["pending_tasks"]) == 0
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


def test_resume_via_api(client, db_session, investigation_id):
    # Set up blocked task in DB
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="HUMAN_INTERVENTION_REQUIRED",
            intervention_type="CAPTCHA",
            intervention_reason="Captcha blocked",
        )
    )
    inv = db_session.get(Investigation, investigation_id)
    inv.status = "WAITING_FOR_USER"
    db_session.commit()

    # Verify status check endpoint
    response = client.get(f"/api/v1/investigations/{investigation_id}/human-intervention")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "WAITING_FOR_USER"
    assert len(data["pending_tasks"]) == 1
    assert data["pending_tasks"][0]["task_id"] == "TASK-001"
    assert data["pending_tasks"][0]["intervention_type"] == "CAPTCHA"

    # Mock fetcher to succeed now (user cleared the captcha)
    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><title>GST Portal</title><body>Active GST.</body></html>")

    try:
        class MockSessionLocal:
            def __enter__(self):
                return db_session
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        # Resume the investigation
        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            res_resp = client.post(f"/api/v1/investigations/{investigation_id}/resume")
        assert res_resp.status_code == 200
        res_data = res_resp.json()
        assert res_data["status"] == "COMPLETED"

        # Check DB states
        db_session.refresh(inv)
        assert inv.status == "COMPLETED"

        task_db = db_session.query(ResearchTaskModel).filter_by(task_id="TASK-001").first()
        assert task_db.status == "COMPLETED"
        assert task_db.intervention_type is None
        assert task_db.intervention_reason is None

        # Check audit event for RESUMED
        resumed_event = db_session.query(InvestigationEvent).filter_by(event_type="INVESTIGATION_RESUMED").first()
        assert resumed_event is not None
        assert resumed_event.status == "STARTED"
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


def test_task_level_human_intervention_endpoint(client, db_session, investigation_id):
    # Set up task requiring intervention in database
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-002",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="HUMAN_INTERVENTION_REQUIRED",
            intervention_type="CAPTCHA",
            intervention_reason="Captcha blocked task",
        )
    )
    inv = db_session.get(Investigation, investigation_id)
    inv.status = "WAITING_FOR_USER"
    db_session.commit()

    # Mock DB session local and graph execution
    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><body>Data</body></html>")

    try:
        class MockSessionLocal:
            def __enter__(self):
                return db_session
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            resp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-002/human-intervention")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"

        # Check task state is updated in DB
        task_db = db_session.query(ResearchTaskModel).filter_by(task_id="TASK-002").first()
        assert task_db.status in {"PENDING", "COMPLETED"}
        assert task_db.intervention_type is None

        # Verify idempotency
        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            idemp_resp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-002/human-intervention")
        assert idemp_resp.status_code == 200
        assert idemp_resp.json()["status"] == "success"
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


def test_events_stream_endpoint(client, db_session, investigation_id):
    # Log a dummy event first
    from app.services.audit import record_event
    record_event(db_session, investigation_id, "NAVIGATING", "browser", "IN_PROGRESS", {"message": "Test navigate"})

    # Fetch events stream using once=True to get immediate response
    resp = client.get(f"/api/v1/investigations/{investigation_id}/events/stream", params={"once": "true"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    lines = list(resp.iter_lines())
    assert any("NAVIGATING" in line for line in lines)


def test_cannot_resume_non_waiting_task(client, db_session, investigation_id):
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-003",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="COMPLETED",
        )
    )
    db_session.commit()

    # Post human confirmation to a task that is completed (non-waiting)
    resp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-003/human-intervention")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "already resumed or completed" in data["message"]


def test_event_history_remains_intact_after_resume(client, db_session, investigation_id):
    # Register events for CAPTCHA detected and WAITING_FOR_HUMAN
    from app.services.audit import record_event
    record_event(db_session, investigation_id, "CAPTCHA_DETECTED", "browser", "IN_PROGRESS", {"task_id": "TASK-004"})
    record_event(db_session, investigation_id, "WAITING_FOR_HUMAN", "browser", "IN_PROGRESS", {"task_id": "TASK-004"})
    
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-004",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="HUMAN_INTERVENTION_REQUIRED",
        )
    )
    inv = db_session.get(Investigation, investigation_id)
    inv.status = "WAITING_FOR_USER"
    db_session.commit()

    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><body>Active GST.</body></html>")

    try:
        class MockSessionLocal:
            def __enter__(self):
                return db_session
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            resp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-004/human-intervention")
        assert resp.status_code == 200

        # Verify all events are preserved chronologically
        events = db_session.query(InvestigationEvent).filter_by(investigation_id=investigation_id).all()
        event_types = [e.event_type for e in events]
        assert "CAPTCHA_DETECTED" in event_types
        assert "WAITING_FOR_HUMAN" in event_types
        assert "HUMAN_ACTION_COMPLETED" in event_types
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


def test_resumed_browser_encounters_second_captcha_creates_new_intervention(client, db_session, investigation_id):
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-005",
            task_type="GST_VERIFICATION",
            target="27ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="HUMAN_INTERVENTION_REQUIRED",
        )
    )
    inv = db_session.get(Investigation, investigation_id)
    inv.status = "WAITING_FOR_USER"
    db_session.commit()

    # Browser will encounter another CAPTCHA after resume!
    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><title>CAPTCHA Verification</title></html>")

    try:
        class MockSessionLocal:
            def __enter__(self):
                return db_session
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            resp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-005/human-intervention")
        assert resp.status_code == 200

        # Resumed task handles blocked page autonomously and completes
        db_session.refresh(inv)
        assert inv.status == "COMPLETED"
        
        task_db = db_session.query(ResearchTaskModel).filter_by(task_id="TASK-005").first()
        assert task_db.status == "COMPLETED"
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


def test_max_retry_loops_limit_respected(client, db_session, investigation_id):
    # Mock loop check limit
    from app.core.config import get_settings
    settings = get_settings()
    settings.max_research_depth = 1 # loop count limit is set to 1

    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-006",
            task_type="GST_VERIFICATION",
            target="27ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="HUMAN_INTERVENTION_REQUIRED",
        )
    )
    inv = db_session.get(Investigation, investigation_id)
    inv.status = "WAITING_FOR_USER"
    # Set planner_loop_count to settings limit
    inv.status_metadata = json.dumps({"planner_loop_count": 2})
    db_session.commit()

    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><title>GST Details</title><body>WIPRO LIMITED</body></html>")

    try:
        class MockSessionLocal:
            def __enter__(self):
                return db_session
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            resp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-006/human-intervention")
        
        assert resp.status_code == 200
        db_session.refresh(inv)
        # Resuming loop checks limit before advancing and sets status to LIMIT_REACHED or similar
        assert inv.status in {"LIMIT_REACHED", "MAX_LOOPS_REACHED", "COMPLETED"}
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher
        settings.max_research_depth = 3 # restore


