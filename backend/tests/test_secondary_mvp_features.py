import json
import pytest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db.base import Base
from app.main import app as fastapi_app
from app.models.investigation import Investigation
from app.models.research_task import ResearchTask as DBResearchTask
from app.models.evidence import Evidence as DBEvidence
from app.agents.browser import BrowserResearchAgent
from app.agents.planner import PlannerAgent
from app.graph.state import ResearchTask as GraphTask


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


def test_epfo_research_planning_and_execution(db_session):
    # Test PlannerAgent creates EPFO task when epfo_code provided
    planner = PlannerAgent()
    state = {
        "raw_input": {"epfo_code": "MH/12345/000"},
        "normalized_input": {"epfo_code": "MH/12345/000"},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
    }
    tasks = planner.plan(state)
    assert len(tasks) == 1
    assert tasks[0].task_type == "EPFO_VERIFICATION"
    assert tasks[0].target == "MH/12345/000"
    assert "epfindia.gov.in" in tasks[0].preferred_sources

    # Test BrowserResearchAgent executes EPFO task
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><title>EPFO Establishment</title><body>Status Active</body></html>")
    results = agent.execute(tasks[0])
    assert len(results) >= 1
    statuses = [r.field_value for r in results if r.field_name == "epfo_status"]
    assert "AVAILABLE" in statuses


def test_investigation_history_api(client, db_session):
    inv = Investigation(
        input_data='{"business_name": "ABC Tech", "epfo_code": "DL/99999"}',
        status="COMPLETED",
        risk_score=25,
        risk_level="LOW",
        user_id="test_user",
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)

    headers = {"Authorization": "Bearer test_user"}

    # 1. Test GET /investigations/ with status filter
    resp = client.get("/api/v1/investigations/?status=COMPLETED", headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == str(inv.id)
    assert items[0]["risk_score"] == 25

    # 2. Test GET /investigations/{id}/history
    hist_resp = client.get(f"/api/v1/investigations/{inv.id}/history", headers=headers)
    assert hist_resp.status_code == 200
    hist_data = hist_resp.json()
    assert hist_data["id"] == str(inv.id)
    assert hist_data["status"] == "COMPLETED"
    assert hist_data["risk_score"] == 25
    assert hist_data["input_data"]["business_name"] == "ABC Tech"


def test_report_export_api(client, db_session):
    inv = Investigation(
        input_data='{"business_name": "Export Corp"}',
        status="COMPLETED",
        risk_score=15,
        risk_level="LOW",
        user_id="test_user",
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)

    headers = {"Authorization": "Bearer test_user"}

    mock_report = {
        "entity": {"business_name": "Export Corp"},
        "entity_confidence": 0.95,
        "overall_risk": {"score": 15, "level": "LOW"},
        "category_scores": {"gst": 0, "mca": 0},
        "major_findings": [],
        "recommendation": "Low risk detected.",
        "evidence_summary": [],
    }

    with mock.patch("app.api.investigations.get_investigation_report", return_value=mock_report):
        # 1. JSON export
        json_resp = client.get(f"/api/v1/investigations/{inv.id}/export?format=json", headers=headers)
        assert json_resp.status_code == 200
        assert "application/json" in json_resp.headers["content-type"]
        assert json_resp.json()["overall_risk"]["score"] == 15

        # 2. CSV export
        csv_resp = client.get(f"/api/v1/investigations/{inv.id}/export/csv", headers=headers)
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers["content-type"]
        assert "Export Corp" in csv_resp.text
        assert "=== RISK SUMMARY ===" in csv_resp.text


def test_captcha_hitl_resume_with_saved_state(client, db_session):
    inv = Investigation(
        input_data='{"gstin": "09ABCDE1234F1Z5"}',
        status="WAITING_FOR_USER",
        user_id="test_user",
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)

    inv.persistent_graph_state = json.dumps({
        "investigation_id": str(inv.id),
        "status": "WAITING_FOR_USER",
        "stop_reason": "Human intervention required: CAPTCHA",
        "pending_tasks": [
            {
                "task_id": "TASK-001",
                "task_type": "GST_VERIFICATION",
                "target": "09ABCDE1234F1Z5",
                "objective": "Verify GST",
                "status": "HUMAN_INTERVENTION_REQUIRED",
                "priority": 1,
                "required_fields": ["legal_name"],
                "preferred_sources": ["gst.gov.in"],
                "fallback_sources": [],
            }
        ],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
    })
    db_session.commit()

    db_task = DBResearchTask(
        investigation_id=inv.id,
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="09ABCDE1234F1Z5",
        objective="Verify GST",
        status="HUMAN_INTERVENTION_REQUIRED",
        intervention_type="CAPTCHA",
        intervention_reason="Captcha blocked",
    )
    db_session.add(db_task)
    db_session.commit()

    headers = {"Authorization": "Bearer test_user"}

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_fetcher = lambda url: "<html><title>GST Portal</title><body>GST is Active.</body></html>"

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_fetcher)), \
         mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
        res = client.post(f"/api/v1/investigations/{inv.id}/resume", headers=headers)

    assert res.status_code == 200
    assert res.json()["status"] == "COMPLETED"
    db_session.refresh(inv)
    assert inv.status == "COMPLETED"
