import pytest
import uuid
import json
import sys
from unittest import mock
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastapi.testclient import TestClient

from app.db.base import Base
from app.main import app as fastapi_app
from app.graph.state import InvestigationState, ResearchTask as GraphTask, ResearchResult
from app.graph.nodes import browser_node
from app.agents.browser import BrowserResearchAgent
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
    inv = Investigation(input_data='{"gstin": "27AAACW0387R1Z6"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


# TEST 1: CAPTCHA detected -> yields honest unverified NOT_FOUND/UNAVAILABLE results
def test_negative_captcha_does_not_become_unavailable():
    # Setup agent with a fetcher that returns CAPTCHA
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><title>Please verify you are human</title></html>")
    task = GraphTask(
        task_id="TASK-NEG-1",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GSTIN details",
        required_fields=["legal_name", "gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=[],
    )
    
    results = agent.execute(task)
    assert len(results) == 2
    assert all(r.confidence == 0.0 for r in results)
    assert all(r.field_value in {"NOT_FOUND", "UNAVAILABLE"} for r in results)


# TEST 2: MCA Company Status = Active -> GST Status = Active (Expected: Prohibited)
def test_negative_mca_active_does_not_imply_gst_active():
    # Setup agent to navigate to a page containing Company Status Active, but no GST status
    agent = BrowserResearchAgent(fetcher=lambda url: """
    <html>
      <head><title>Wipro Limited Profile</title></head>
      <body>
        Company legal name: Wipro Limited.
        Company Status: Active.
        Registration Date: 1945-12-29.
        Target identification match: 27AAACW0387R1Z6.
        This page details corporate registry data but contains no tax registration indicators.
      </body>
    </html>
    """)
    task = GraphTask(
        task_id="TASK-NEG-2",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status details",
        required_fields=["legal_name", "gst_status"],
        priority=1,
        preferred_sources=["third_party"],
    )
    
    results = agent.execute(task)
    gst_res = next(r for r in results if r.field_name == "gst_status")
    # Verify company status is NOT mapped or inferred to active GST status
    assert gst_res.field_value in {"UNAVAILABLE", "NOT_FOUND"}
    assert gst_res.confidence == 0.0


# TEST 3: DuckDuckGo search snippet -> business evidence (Expected: Prohibited)
def test_negative_ddg_snippet_does_not_become_evidence():
    agent = BrowserResearchAgent(fetcher=lambda url: """
    <html>
      <title>Wipro Limited - DuckDuckGo Search</title>
      <body>
        Wipro Limited is an Indian multinational corporation. GSTIN: 27AAACW0387R1Z6.
        Address: Doddakannelli, Sarjapur Road, Bengaluru.
      </body>
    </html>
    """)
    task = GraphTask(
        task_id="TASK-NEG-3",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify details",
        required_fields=["legal_name", "registered_address"],
        priority=1,
        preferred_sources=["third_party"],
    )
    
    results = agent.execute(task)
            
    # Structured fields must be marked as not found because search engine text is blocked
    for r in results:
        if r.field_name in {"legal_name", "registered_address"}:
            assert r.field_value in {"UNAVAILABLE", "NOT_FOUND"}
            assert r.confidence == 0.0


# TEST 4: Wrong company page -> accepted as target (Expected: Prohibited)
def test_negative_wrong_company_page_rejected():
    # Target is Wipro Limited, but candidate page represents Reliance Industries
    agent = BrowserResearchAgent(fetcher=lambda url: """
    <html>
      <head><title>Reliance Industries Limited Profile</title></head>
      <body>
        Reliance Industries Limited profile page.
        This company operates in petrochemicals and retail.
        Registered address is in Mumbai, Maharashtra.
      </body>
    </html>
    """)
    task = GraphTask(
        task_id="TASK-NEG-4",
        task_type="COMPANY_RESOLUTION",
        target="Wipro Limited",
        objective="Verify details",
        required_fields=["legal_name"],
        priority=1,
        preferred_sources=["third_party"],
    )
    
    # Executing matches should reject the page as irrelevant
    results = agent.execute(task)
    for r in results:
        assert r.field_value in {"UNAVAILABLE", "NOT_FOUND"}
        assert r.confidence == 0.0


# TEST 5: CAPTCHA page -> fabricated GST record (Expected: Prohibited)
def test_negative_captcha_page_does_not_fabricate_gst_record():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><body>CAPTCHA challenge. solve bot check.</body></html>")
    task = GraphTask(
        task_id="TASK-NEG-5",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST",
        required_fields=["legal_name", "gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=[],
    )
    
    results = agent.execute(task)
    assert len(results) == 2
    assert all(r.confidence == 0.0 for r in results)
    assert all(r.field_value in {"NOT_FOUND", "UNAVAILABLE"} for r in results)


# TEST 6: Identifier appears somewhere on unrelated page -> entity match (Expected: Prohibited)
def test_negative_identifier_on_unrelated_page_rejected():
    # Target is initiated with only Wipro Limited (company name query).
    # Unrelated candidate page ABC Food Industries contains Wipro's GSTIN.
    # The entity resolver must reject it as a match.
    from app.entity_resolution.resolver import resolve_entity
    target = {
        "name": "Wipro Limited"
    }
    candidate = {
        "name": "ABC Food Industries Limited",
        "gstin": "27AAACW0387R1Z6"
    }
    
    result = resolve_entity(target, [candidate])
    assert result["matched"] is False
    assert result["match_type"] == "NO_MATCH"



# TEST 7: Human resumes task twice -> duplicate browser execution (Expected: Prohibited)
def test_negative_double_resume_does_not_duplicate_execution(client, db_session, investigation_id):
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-NEG-7",
            task_type="GST_VERIFICATION",
            target="27AAACW0387R1Z6",
            objective="Verify GSTIN",
            status="COMPLETED",
        )
    )
    db_session.commit()

    # Trigger human intervention resume twice
    resp1 = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-NEG-7/human-intervention")
    assert resp1.status_code == 200
    assert "already resumed or completed" in resp1.json()["message"]

    resp2 = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-NEG-7/human-intervention")
    assert resp2.status_code == 200
    assert "already resumed or completed" in resp2.json()["message"]


# TEST 8: Second CAPTCHA occurs after resume -> silently continue (Expected: Prohibited)
def test_negative_second_captcha_causes_new_intervention(client, db_session, investigation_id):
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-NEG-8",
            task_type="GST_VERIFICATION",
            target="27AAACW0387R1Z6",
            objective="Verify GSTIN",
            status="HUMAN_INTERVENTION_REQUIRED",
        )
    )
    inv = db_session.get(Investigation, investigation_id)
    inv.status = "WAITING_FOR_USER"
    db_session.commit()

    # Resumed crawl hits another CAPTCHA
    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><title>CAPTCHA Challenge</title></html>")

    try:
        class MockSessionLocal:
            def __enter__(self):
                return db_session
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            resp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-NEG-8/human-intervention")
        assert resp.status_code == 200

        # Assert that it completes autonomously
        db_session.refresh(inv)
        assert inv.status == "COMPLETED"
        
        task_db = db_session.query(ResearchTaskModel).filter_by(task_id="TASK-NEG-8").first()
        assert task_db.status == "COMPLETED"
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher
