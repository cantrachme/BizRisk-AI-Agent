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


def test_captcha_raises_exception():
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
    with pytest.raises(HumanInterventionRequiredException) as ex:
        agent.execute(task)
    assert ex.value.intervention_type == "CAPTCHA"


def test_otp_raises_exception():
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
    with pytest.raises(HumanInterventionRequiredException) as ex:
        agent.execute(task)
    assert ex.value.intervention_type == "OTP"


def test_login_raises_exception():
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
    with pytest.raises(HumanInterventionRequiredException) as ex:
        agent.execute(task)
    assert ex.value.intervention_type == "LOGIN_REQUIRED"


def test_browser_node_handles_hitl(db_session, investigation_id):
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
        
        assert out_state["status"] == "WAITING_FOR_USER"
        assert out_state["stop_reason"] == "Human intervention required: CAPTCHA"
        assert len(out_state["pending_tasks"]) == 1
        assert out_state["pending_tasks"][0].status == "HUMAN_INTERVENTION_REQUIRED"

        # Check database persistence
        task_db = db_session.query(ResearchTaskModel).filter_by(task_id="TASK-001").first()
        assert task_db.status == "HUMAN_INTERVENTION_REQUIRED"
        assert task_db.intervention_type == "CAPTCHA"
        assert "CAPTCHA" in task_db.intervention_reason

        # Check audit event persistence
        event = db_session.query(InvestigationEvent).filter_by(event_type="HUMAN_INTERVENTION_REQUIRED").first()
        assert event is not None
        assert event.status == "WAITING_FOR_USER"
        meta = json.loads(event.metadata_json)
        assert meta["task_id"] == "TASK-001"
        assert meta["type"] == "CAPTCHA"
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
        
        # WAITING_FOR_USER since task 1 is blocked
        assert out_state["status"] == "WAITING_FOR_USER"
        # Task 2 completed successfully!
        assert len(out_state["completed_tasks"]) == 1
        assert out_state["completed_tasks"][0].task_id == "TASK-002"
        # Task 1 remains pending but marked as HUMAN_INTERVENTION_REQUIRED
        assert len(out_state["pending_tasks"]) == 1
        assert out_state["pending_tasks"][0].task_id == "TASK-001"
        assert out_state["pending_tasks"][0].status == "HUMAN_INTERVENTION_REQUIRED"
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
