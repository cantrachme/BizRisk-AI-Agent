import pytest
from unittest import mock
import json
import re
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask
from app.models.research_task import ResearchTask as ResearchTaskModel
from app.models.source_registry import SourceRegistry
from app.models.investigation import Investigation
from app.models.investigation_event import InvestigationEvent
from app.graph.nodes import browser_node
from app.graph.state import InvestigationState
from app.core.exceptions import HumanInterventionRequiredException
from app.db.base import Base
from app.main import app as fastapi_app


@pytest.fixture
def db_session():
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
    
    # Register sources
    session.add_all([
        SourceRegistry(name="zaubacorp.com", type="GST_VERIFICATION", domain="zaubacorp.com", enabled=True, config_json='{"confidence": 0.50}'),
        SourceRegistry(name="zaubacorp.com", type="GENERAL_WEB_RESEARCH", domain="zaubacorp.com", enabled=True, config_json='{"confidence": 0.50}'),
        SourceRegistry(name="unrelated.com", type="GST_VERIFICATION", domain="unrelated.com", enabled=True, config_json='{"confidence": 0.50}'),
        SourceRegistry(name="similar.com", type="GENERAL_WEB_RESEARCH", domain="similar.com", enabled=True, config_json='{"confidence": 0.50}'),
        SourceRegistry(name="timeout.com", type="GST_VERIFICATION", domain="timeout.com", enabled=True, config_json='{"confidence": 0.50}'),
        SourceRegistry(name="duckduckgo.com", type="GENERAL_WEB_RESEARCH", domain="duckduckgo.com", enabled=True, config_json='{"confidence": 0.50}'),
    ])
    session.commit()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    from app.db import get_db
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    fastapi_app.dependency_overrides[get_db] = override_get_db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def investigation_id(db_session):
    inv = Investigation(input_data='{"gstin": "09ABCDE1234F1Z5"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


@pytest.fixture(autouse=True)
def mock_session_local(db_session):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
        yield


@pytest.fixture
def agent():
    return BrowserResearchAgent()


# TEST A: Official GST page -> CAPTCHA
def test_scenario_a_gst_captcha(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-A",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status",
        required_fields=["legal_name", "gst_status", "registered_address"],
        preferred_sources=["gst.gov.in"],
        fallback_sources=[],
        priority=1,
    )
    
    def mock_fetcher(url):
        return "<html><body>Please solve the captcha below to proceed.</body></html>"
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    assert len(results) == 3
    assert all(r.confidence == 0.0 for r in results)
    assert all(r.field_value in {"NOT_FOUND", "UNAVAILABLE"} for r in results)


# TEST B: GST CAPTCHA -> Fallback to ZaubaCorp directly
def test_scenario_b_gst_captcha_fallback_ddg(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-B",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status",
        required_fields=["legal_name", "gst_status"],
        preferred_sources=["gst.gov.in"],
        fallback_sources=["zaubacorp.com"],
        priority=1,
    )
    
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><body>please verify you are human (captcha)</body></html>"
        elif "zaubacorp.com" in url:
            return """
            <html>
              <head><title>Wipro Limited - Profile</title></head>
              <body>
                Target identifier match: 27AAACW0387R1Z6.
                Legal Name: Wipro Limited.
                Company Status: Active.
              </body>
            </html>
            """
        return ""
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "Wipro Limited"
    assert legal_name_res.source_name == "zaubacorp.com"
    assert legal_name_res.confidence == 0.50


# TEST C: ZaubaCorp page says "Company Status: Active" -> gst_status remains UNAVAILABLE
def test_scenario_c_company_status_active_does_not_imply_gst_status(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-C",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status",
        required_fields=["company_status", "gst_status"],
        preferred_sources=["zaubacorp.com"],
        fallback_sources=[],
        priority=1,
    )
    
    def mock_fetcher(url):
        return """
        <html>
          <head><title>Wipro Limited - Profile</title></head>
          <body>
            Target identifier match: 27AAACW0387R1Z6.
            Company Status: Active.
            Director status is active.
          </body>
        </html>
        """
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    
    company_status_res = next(r for r in results if r.field_name == "company_status")
    gst_status_res = next(r for r in results if r.field_name == "gst_status")
    
    assert company_status_res.field_value == "ACTIVE"
    assert gst_status_res.field_value == "UNAVAILABLE"
    assert gst_status_res.confidence == 0.0


# TEST D: ZaubaCorp title normalization
def test_scenario_d_title_normalization(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-D",
        task_type="GENERAL_WEB_RESEARCH",
        target="Wipro Limited",
        objective="Verify name",
        required_fields=["legal_name"],
        preferred_sources=["zaubacorp.com"],
        fallback_sources=[],
        priority=1,
    )
    
    def mock_fetcher(url):
        return """
        <html>
          <head><title>Wipro Limited - Company Profile, Shareholders, Directors</title></head>
          <body>
            Wipro Limited has served clients for decades.
          </body>
        </html>
        """
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "Wipro Limited"


# TEST E: Search engine page "DuckDuckGo - Protection. Privacy. Peace of mind." -> NEVER legal_name
def test_scenario_e_search_engine_text_never_becomes_legal_name(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-E",
        task_type="GENERAL_WEB_RESEARCH",
        target="DuckDuckGo",
        objective="Verify name",
        required_fields=["legal_name"],
        preferred_sources=["duckduckgo.com"],
        fallback_sources=[],
        priority=1,
    )
    
    def mock_fetcher(url):
        return """
        <html>
          <head><title>DuckDuckGo - Privacy. Protection. Peace of mind.</title></head>
          <body>
            We do not track you.
          </body>
        </html>
        """
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "NOT_FOUND"


# TEST F: Page contains GSTIN but no company identity match -> IRRELEVANT
def test_scenario_f_gstin_identity_mismatch(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-F",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GSTIN",
        required_fields=["legal_name"],
        preferred_sources=["unrelated.com"],
        fallback_sources=[],
        priority=1,
    )
    
    def mock_fetcher(url):
        return """
        <html>
          <head><title>Other Company Profile</title></head>
          <body>
            This is completely unrelated business. 
            We are operating in the state of Maharashtra. 
            Our registration number is 27ABCDE1234F1Z5. 
            We do not match the target at all.
            We specialize in providing high-quality custom items. 
            For any queries, please check our detailed info below.
            We hope to hear from you soon.
          </body>
        </html>
        """
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "NOT_FOUND"
    assert legal_name_res.confidence == 0.0


# TEST G: Page contains similar company name but wrong address/GSTIN -> IRRELEVANT
def test_scenario_g_similar_name_mismatch(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-G",
        task_type="GENERAL_WEB_RESEARCH",
        target="ABC Foods Private Limited",
        objective="Verify company details",
        required_fields=["legal_name"],
        preferred_sources=["similar.com"],
        fallback_sources=[],
        priority=1,
    )
    
    def mock_fetcher(url):
        return """
        <html>
          <head><title>ABC Food Industries Limited</title></head>
          <body>
            This is a profile of ABC Food Industries Limited.
            We specialize in agricultural food supply chains.
            We operate out of Gujarat, India.
            Please read our latest reports and updates.
            We are a completely independent entity.
          </body>
        </html>
        """
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "NOT_FOUND"
    assert legal_name_res.confidence == 0.0


# TEST H: Requested field absent -> NOT_FOUND/UNAVAILABLE + confidence 0.0
def test_scenario_h_absent_fields(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-H",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify details",
        required_fields=["registered_address"],
        preferred_sources=["zaubacorp.com"],
        fallback_sources=[],
        priority=1,
    )
    
    def mock_fetcher(url):
        return """
        <html>
          <head><title>Wipro Limited</title></head>
          <body>
            Target identifier: 27AAACW0387R1Z6.
            Active registry information.
            No address is published on this public page.
          </body>
        </html>
        """
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    address_res = next(r for r in results if r.field_name == "registered_address")
    assert address_res.field_value == "NOT_FOUND"
    assert address_res.confidence == 0.0


# TEST I: Human intervention resume called twice -> Idempotent
def test_scenario_i_idempotency_resumption(client, db_session, investigation_id):
    # Set up task requiring intervention
    db_session.add(
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-I",
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

    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(lambda url: "<html><body>Data</body></html>")

    try:
        class MockSessionLocal:
            def __enter__(self):
                return db_session
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with mock.patch("app.db.session.SessionLocal", MockSessionLocal), mock.patch("app.services.qa.validate_report", return_value={"status": "PASS", "issues": []}):
            # First call
            resp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-I/human-intervention")
            assert resp.status_code == 200
            
            # Second call
            resp_idemp = client.post(f"/api/v1/investigations/{investigation_id}/tasks/TASK-I/human-intervention")
            assert resp_idemp.status_code == 200
            assert resp_idemp.json()["status"] == "success"
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


# TEST J: Two browser tasks run while one is blocked -> Unblocked continues
def test_scenario_j_unblocked_task_continues(db_session, investigation_id):
    db_session.add_all([
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-J1",
            task_type="GST_VERIFICATION",
            target="09ABCDE1234F1Z5",
            objective="Verify GSTIN",
            status="PENDING",
        ),
        ResearchTaskModel(
            investigation_id=investigation_id,
            task_id="TASK-J2",
            task_type="MCA_VERIFICATION",
            target="L32102KA1945PLC020800",
            objective="Verify MCA",
            status="PENDING",
        ),
    ])
    db_session.commit()

    state = InvestigationState(
        investigation_id=str(investigation_id),
        status="PENDING_RESEARCH",
        pending_tasks=[
            ResearchTask(
                task_id="TASK-J1",
                task_type="GST_VERIFICATION",
                target="09ABCDE1234F1Z5",
                objective="Verify GSTIN",
                status="PENDING",
                priority=1,
                required_fields=["legal_name"],
                preferred_sources=["gst.gov.in"],
                fallback_sources=[],
            ),
            ResearchTask(
                task_id="TASK-J2",
                task_type="MCA_VERIFICATION",
                target="L32102KA1945PLC020800",
                objective="Verify MCA",
                status="PENDING",
                priority=2,
                required_fields=["legal_name"],
                preferred_sources=["mca.gov.in"],
                fallback_sources=[],
            ),
        ],
        completed_tasks=[],
        risk_score=None,
        report=None,
        audit_trail=[],
        stop_reason=None,
        current_node=None,
        error_count=0,
    )
    
    # Task J1 fails with CAPTCHA, Task J2 succeeds
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><body>please verify you are human (captcha)</body></html>"
        else:
            return "<html><body>MCA details for L32102KA1945PLC020800 active</body></html>"

    original_fetcher = BrowserResearchAgent._fetch_page
    BrowserResearchAgent._fetch_page = staticmethod(mock_fetcher)

    try:
        class MockSessionLocal:
            def __enter__(self):
                return db_session
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
            next_state = browser_node(state)
            
            # Both tasks complete autonomously (J1 as unverified, J2 as verified)
            assert len(next_state["completed_tasks"]) == 2
            t1 = next(t for t in next_state["completed_tasks"] if t.task_id == "TASK-J1")
            assert t1.status == "COMPLETED"
            
            t2 = next(t for t in next_state["completed_tasks"] if t.task_id == "TASK-J2")
            assert t2.status == "COMPLETED"
    finally:
        BrowserResearchAgent._fetch_page = original_fetcher


# TEST K: Direct directory result is irrelevant -> Reject and try next registered fallback
def test_scenario_k_irrelevant_result_ignored(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-K",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status",
        required_fields=["legal_name"],
        preferred_sources=["unrelated.com"],
        fallback_sources=["zaubacorp.com"],
        priority=1,
    )
    
    def mock_fetcher(url):
        if "unrelated.com" in url:
            return """
            <html>
              <head><title>Irrelevant Company Profile</title></head>
              <body>
                This is unrelated. Delhi transport business profile.
                 Delhi transport has been operating for 10 years.
                We have over 100 trucks in our commercial fleet.
              </body>
            </html>
            """
        elif "zaubacorp.com" in url:
            return """
            <html>
              <head><title>Wipro Limited</title></head>
              <body>
                Target identifier match: 27AAACW0387R1Z6.
                Here is the company name Wipro Limited.
              </body>
            </html>
            """
        return ""
        
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "Wipro Limited"
    assert legal_name_res.source_name == "zaubacorp.com"


# TEST L: Network timeout on one source -> Handled gracefully without crash
def test_scenario_l_network_timeout(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-L",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify details",
        required_fields=["legal_name"],
        preferred_sources=["timeout.com"],
        fallback_sources=["zaubacorp.com"],
        priority=1,
    )
    
    def mock_fetcher(url):
        if "timeout.com" in url:
            raise Exception("Connection timed out (mock error)")
        else:
            return """
            <html>
              <head><title>Wipro Limited</title></head>
              <body>
                Target match: 27AAACW0387R1Z6.
                Wipro Limited company name.
              </body>
            </html>
            """
            
    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "Wipro Limited"
    assert legal_name_res.source_name == "zaubacorp.com"


# TEST M: Intermediate CAPTCHAs on multiple fallbacks before success
def test_scenario_m_intermediate_fallback_captcha(agent, db_session, investigation_id):
    task = ResearchTask(
        task_id="TASK-M",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify details",
        required_fields=["legal_name"],
        preferred_sources=["timeout.com"],
        fallback_sources=["unrelated.com", "zaubacorp.com"],
        priority=1,
    )

    def mock_fetcher(url):
        if "timeout.com" in url:
            return "<html><title>CAPTCHA challenge</title><body>Please solve the cloudflare verification.</body></html>"
        elif "zaubacorp.com" in url:
            return """
            <html>
              <head><title>Wipro Limited</title></head>
              <body>
                Target match: 27AAACW0387R1Z6.
                Legal Name: Wipro Limited.
              </body>
            </html>
            """
        return ""

    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "Wipro Limited"
    assert legal_name_res.source_name == "zaubacorp.com"


# TEST N: Deterministic directory retrieval
def test_scenario_n_complex_redirect_chains(agent, investigation_id):
    task = ResearchTask(
        task_id="TASK-N",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify details",
        required_fields=["legal_name"],
        preferred_sources=["zaubacorp.com"],
        fallback_sources=[],
        priority=1,
    )

    def mock_fetcher(url):
        if "zaubacorp.com" in url:
            return """
            <html>
              <head><title>Wipro Limited</title></head>
              <body>
                Target match: 27AAACW0387R1Z6.
                Wipro Limited profile.
              </body>
            </html>
            """
        return ""

    agent.fetcher = mock_fetcher
    results = agent.execute(investigation_id=investigation_id, task=task)
    legal_name_res = next(r for r in results if r.field_name == "legal_name")
    assert legal_name_res.field_value == "Wipro Limited"
    assert legal_name_res.source_name == "zaubacorp.com"

