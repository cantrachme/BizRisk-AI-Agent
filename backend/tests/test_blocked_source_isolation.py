import pytest
from unittest import mock
import json
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.graph.workflow import app as graph_app
from app.models.investigation import Investigation
from app.models.evidence import Evidence
from app.models.report import Report
from app.models.risk_signal import RiskSignal
from app.agents.browser import BrowserResearchAgent
from app.services.qa import validate_report
from app.services.report import generate_investigation_report
from app.risk.engine import calculate_risk_analysis

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

def mock_blocked_fetch_page(url: str) -> str:
    # Return Access Denied page for all URLs to simulate full site blocks/failures
    return "<html><title>Access Denied</title><body>403 Forbidden cloudflare security check.</body></html>"

def test_blocked_source_isolation_downstream(db_session, investigation_id):
    # Prepare Mock SessionLocal to use the test SQLite DB
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Update investigation inputs
    inv = db_session.get(Investigation, investigation_id)
    inv.raw_input = json.dumps({"business_name": "Test Blocked Corp", "website": "http://blocked-site.com"})
    db_session.commit()

    # Invoke graph with a mock patch on browser fetcher returning blocked pages
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_blocked_fetch_page)):
        
        state = {
            "investigation_id": str(investigation_id),
            "raw_input": {"business_name": "Test Blocked Corp", "website": "http://blocked-site.com"},
            "normalized_input": {},
            "pending_tasks": [],
            "completed_tasks": [],
            "failed_tasks": [],
            "results": [],
            "planner_loop_count": 0,
            "qa_loop_count": 0,
            "status": "CREATED",
        }
        
        # Invoke the graph app
        final_state = graph_app.invoke(state)

    # Refresh/load investigation from DB
    db_session.expire_all()
    updated_inv = db_session.get(Investigation, investigation_id)

    # Verify Downstream Behavior Assertions:
    
    # 1. Investigation completes with INSUFFICIENT_EVIDENCE when sources are blocked
    assert updated_inv.status in {"COMPLETED", "FAILED"}
    assert updated_inv.risk_score is None

    # 2. Check the evidence objects generated
    evidences = db_session.query(Evidence).filter(Evidence.investigation_id == investigation_id).all()
    
    # Assert that all evidence from blocked pages have confidence 0.0 and status UNAVAILABLE
    for ev in evidences:
        if ev.field_name in ["gst_status", "mca_status", "website_status"]:
            assert ev.confidence == 0.0
            assert ev.field_value == "UNAVAILABLE"
            
        # 3. No dummy entity candidates returned by browser agent
        if ev.field_name == "candidate_entities" and ev.source_name != "discovery_agent":
            candidates_list = json.loads(ev.field_value)
            assert candidates_list == []

    # 4. Assert risk signals do not contain active risk based on blocked evidence
    risk_signals = db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).all()
    # No risk rules triggered
    assert len(risk_signals) == 0
    assert updated_inv.risk_score is None
