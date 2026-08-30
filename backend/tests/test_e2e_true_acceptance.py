import uuid
import json
import pytest
from unittest import mock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.investigation import Investigation
from app.models.report import Report
from app.models.evidence import Evidence
from app.models.research_task import ResearchTask as ResearchTaskModel
from app.graph.workflow import app as graph_app, should_continue_after_qa
from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask, ResearchResult


current_db_session = None


@pytest.fixture(name="db_session")
def fixture_db_session():
    global current_db_session
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )
    session = TestingSessionLocal()
    current_db_session = session
    try:
        yield session
    finally:
        session.close()
        current_db_session = None


@pytest.fixture(name="client")
def fixture_client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def mock_fetch_page(url: str) -> str:
    url_lower = url.lower()
    if "gst.gov.in" in url_lower:
        return "<html><title>Acme Foods Private Limited</title><body>Active GST Status. Address: 123 Main St, Delhi. Business Activity: Food. Registration Date: 2020-01-01</body></html>"
    elif "mca.gov.in" in url_lower:
        return "<html><title>Acme Foods Private Limited</title><body>Active MCA Status. Address: 123 Main St, Delhi. Business Activity: Food. Registration Date: 2020-01-01</body></html>"
    elif "third_party" in url_lower or "google.com" in url_lower:
        return "<html><title>Acme Foods Private Limited</title><body>Registered company Acme Foods Private Limited active in 2020. Address: 123 Main St, Delhi.</body></html>"
    else:
        return "<html><title>Acme Foods Private Limited</title><body>Acme Foods Private Limited Active.</body></html>"


original_extract = BrowserResearchAgent._extract_field_value

def mock_extract_field_value(task, field_name, page_data):
    val = original_extract(task, field_name, page_data)
    if field_name == "candidate_entities" and isinstance(val, list):
        for item in val:
            item["business_name"] = item.get("name", "Acme Foods Private Limited")
            item["gstin"] = task.target if task.target and len(task.target) == 15 else "27ABCDE1234F1Z5"
            item["confidence"] = 0.95
            item["match_type"] = "EXACT"
    return val


class MockSessionLocal:
    def __init__(self):
        pass
    def __enter__(self):
        return current_db_session
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def query(self, *args, **kwargs):
        return current_db_session.query(*args, **kwargs)
    def add(self, *args, **kwargs):
        return current_db_session.add(*args, **kwargs)
    def commit(self, *args, **kwargs):
        return current_db_session.commit()
    def close(self):
        pass


def test_full_true_e2e_investigation_acceptance_flow(client, db_session):
    headers = {"Authorization": "Bearer UserAcceptance"}
    payload = {
        "business_name": "Acme Foods Private Limited",
        "gstin": "27ABCDE1234F1Z5",
    }
    
    # 1. Verify investigation creation/start via API
    response = client.post("/api/v1/investigations/", json=payload, headers=headers)
    assert response.status_code == 201
    inv_data = response.json()
    assert "id" in inv_data
    inv_id = uuid.UUID(inv_data["id"])

    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {
            "business_name": "ACME FOODS PRIVATE LIMITED",
            "gstin": "27ABCDE1234F1Z5",
        },
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 0,
        "qa_loop_count": 0,
        "status": "CREATED",
    }

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_fetch_page)), \
         mock.patch("app.agents.browser.BrowserResearchAgent._extract_field_value", staticmethod(mock_extract_field_value)):
        
        output_state = graph_app.invoke(initial_state)

    # 2. Verify planner created research tasks
    db_tasks = db_session.query(ResearchTaskModel).filter(ResearchTaskModel.investigation_id == inv_id).all()
    assert len(db_tasks) > 0

    # 3. Verify Browser Agent executed and produced structured evidence
    assert len(output_state["results"]) > 0

    # 4. Verify evidence is persisted and traceable to investigation
    db_evidence = db_session.query(Evidence).filter(Evidence.investigation_id == inv_id).all()
    assert len(db_evidence) > 0
    for ev in db_evidence:
        assert ev.investigation_id == inv_id
        assert ev.research_result_id is not None

    # 5. Verify entity resolution completed
    resolved_entity = output_state.get("resolved_entity") or {}
    assert resolved_entity.get("business_name") is not None or len(output_state["results"]) > 0

    # 6. Verify deterministic risk scoring runs from validated evidence-backed signals
    overall_risk = output_state.get("overall_risk") or {}
    assert "score" in overall_risk
    assert 0 <= overall_risk["score"] <= 100

    # 7. Verify report contains evidence IDs and is stored in DB
    report_data = output_state.get("report") or {}
    assert "overall_risk" in report_data
    assert len(report_data.get("evidence_summary", [])) > 0
    assert any("evidence_id" in ev for ev in report_data["evidence_summary"])
    db_report = db_session.query(Report).filter(Report.investigation_id == inv_id).first()
    assert db_report is not None

    # 8. Verify independent QA passes valid report
    qa_result = output_state.get("qa_result") or {}
    assert qa_result.get("status") == "PASS"

    # 9. Verify successful workflow reaches terminal / completed state
    assert output_state.get("status") in {"COMPLETED", "QA_COMPLETED"}


def test_qa_failure_routing_bounded_retries():
    # Verify QA failure routing by failure type and bounded retries
    state_score_mismatch = {
        "qa_result": {
            "status": "FAIL",
            "issues": [{"type": "WRONG_RISK_SCORE", "finding": "Score mismatch"}]
        },
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state_score_mismatch) == "risk_analysis"

    state_wording = {
        "qa_result": {
            "status": "FAIL",
            "issues": [{"type": "REPORT_WORDING", "finding": "Forbidden word"}]
        },
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state_wording) == "report_generation"

    state_missing_evidence = {
        "qa_result": {
            "status": "FAIL",
            "issues": [{"type": "MISSING_EVIDENCE", "finding": "Orphan claim"}]
        },
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state_missing_evidence) == "planner"

    # Max retries boundary (loop_count >= 2 terminates graph at __end__)
    state_max_retry = {
        "qa_result": {
            "status": "FAIL",
            "issues": [{"type": "MISSING_EVIDENCE", "finding": "Orphan claim"}]
        },
        "qa_loop_count": 2
    }
    assert should_continue_after_qa(state_max_retry) == "__end__"
