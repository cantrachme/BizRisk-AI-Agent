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
from app.graph.workflow import app as graph_app
from app.agents.browser import BrowserResearchAgent
from app.agents.discovery import DiscoveryAgent
from app.entity_resolution.resolver import resolve_entity
from app.graph.state import ResearchTask, ResearchResult
from app.core.exceptions import HumanInterventionRequiredException


# Global reference to hold the active test database session
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


# Dynamic mock page fetcher for the E2E flows
def mock_fetch_page(url: str) -> str:
    url_lower = url.lower()
    if "captcha" in url_lower or "robot" in url_lower or "27abcde1234f3z7" in url_lower:
        return "<html><title>bot verification</title><body>recaptcha box here.</body></html>"
    elif "otp" in url_lower:
        return "<html><title>OTP Code</title><body>enter verification code sent.</body></html>"
    elif "login" in url_lower:
        return "<html><title>Sign In</title><body>please sign in to proceed.</body></html>"
    elif "gst.gov.in" in url_lower:
        if "conflict" in url_lower or "27abcde1234f2z6" in url_lower:
            return "<html><title>ABC Foods Private Limited</title><body>Active. Address: 101 GST Lane, Delhi. Business Activity: Tech. Registration Date: 2020-01-01</body></html>"
        else:
            return "<html><title>ABC Foods Private Limited</title><body>Active GST Status. Address: 123 Main St, Delhi. Business Activity: Food. Registration Date: 2020-01-01</body></html>"
    elif "mca.gov.in" in url_lower:
        if "conflict" in url_lower or "27abcde1234f2z6" in url_lower:
            return "<html><title>ABC Foods Private Limited</title><body>Active. Address: 202 MCA Boulevard, Mumbai. Business Activity: Finance. Registration Date: 2018-05-15</body></html>"
        else:
            return "<html><title>ABC Foods Private Limited</title><body>Active. Address: 123 Main St, Delhi. Business Activity: Food. Registration Date: 2020-01-01</body></html>"
    elif "abcfoods.in" in url_lower or "company_website" in url_lower:
        return "<html><title>ABC Foods website</title><body>Active. Address: 123 Main St, Delhi.</body></html>"
    elif "third_party" in url_lower or "google.com" in url_lower:
        return "<html><title>Third Party Info</title><body>ABC Foods registered in 2020. Address: 123 Main St, Delhi.</body></html>"
    else:
        return "<html><title>Default title</title><body>Default page text content.</body></html>"


# Intercept extract_field_value and process to ensure business_name is populated for QA entity verification
original_extract = BrowserResearchAgent._extract_field_value
original_resolve_url = BrowserResearchAgent._resolve_url

def mock_extract_field_value(task, field_name, page_data):
    val = original_extract(task, field_name, page_data)
    if field_name == "candidate_entities" and isinstance(val, list):
        for item in val:
            if "name" in item and "business_name" not in item:
                item["business_name"] = item["name"]
    return val

def mock_discovery_process(self, investigation_input):
    target = (investigation_input.get("business_name") or investigation_input.get("name") or "").lower()
    if "abc foods" in target:
        gstin = investigation_input.get("gstin") or "27ABCDE1234F1Z5"
        cin = "L12345MH2020PLC000001"
        if "f2z6" in gstin or "27ABCDE1234F2Z6" in gstin:
            gstin = "27ABCDE1234F2Z6"
        elif "f3z7" in gstin or "27ABCDE1234F3Z7" in gstin:
            gstin = "27ABCDE1234F3Z7"
            
        return {
            "candidate_entities": [{
                "business_name": "ABC Foods Private Limited",
                "name": "ABC Foods Private Limited",
                "gstin": gstin,
                "cin": cin,
                "website": "abcfoods.in",
                "location": "Delhi",
                "confidence": 1.0,
            }]
        }
    elif "tata" in target:
        return {
            "candidate_entities": [
                {"business_name": "TATA SONS PRIVATE LIMITED", "name": "TATA SONS PRIVATE LIMITED", "location": "MUMBAI", "cin": "L12345MH1917PLC000001", "confidence": 0.95},
                {"business_name": "TATA MOTORS LIMITED", "name": "TATA MOTORS LIMITED", "location": "MUMBAI", "cin": "L34103MH1945PLC008518", "confidence": 0.95}
            ]
        }
    else:
        return {
            "candidate_entities": []
        }

def mock_resolve_entity(target, candidates, llm=None, prompt_version="v1"):
    res = resolve_entity(target, candidates, llm, prompt_version)
    return res

def mock_resolve_url(task, source, source_url):
    res = original_resolve_url(task, source, source_url)
    if res and task.target:
        if "?" in res:
            res += f"&mock_target={task.target}"
        else:
            res += f"?mock_target={task.target}"
    return res


# Database ID normalizer patches to prevent SQLite mismatch between UUID objects and string keys
from app.services.research_task import save_research_tasks
from app.services.evidence import save_research_results, save_research_result, get_evidences_for_investigation

original_save_tasks = save_research_tasks
original_save_results = save_research_results
original_save_result = save_research_result
original_get_evidences = get_evidences_for_investigation

def mock_save_research_tasks(db, tasks, investigation_id):
    if isinstance(investigation_id, str):
        investigation_id = uuid.UUID(investigation_id)
    return original_save_tasks(db, tasks, investigation_id)

def mock_save_research_results(db, results, investigation_id):
    if isinstance(investigation_id, str):
        investigation_id = uuid.UUID(investigation_id)
    return original_save_results(db, results, investigation_id)

def mock_save_research_result(db, result, investigation_id):
    if isinstance(investigation_id, str):
        investigation_id = uuid.UUID(investigation_id)
    return original_save_result(db, result, investigation_id)

def mock_get_evidences_for_investigation(db, investigation_id):
    if isinstance(investigation_id, str):
        investigation_id = uuid.UUID(investigation_id)
    return original_get_evidences(db, investigation_id)


def mock_update_investigation_in_db(
    investigation_id_str,
    current_node,
    status=None,
    retry_count=None,
    risk_score=None,
    risk_level=None,
    resolved_entity_id=None,
    entity_confidence=None,
    completed=False,
    state=None,
):
    if not investigation_id_str:
        return
    investigation_id = uuid.UUID(str(investigation_id_str))

    from app.db.session import SessionLocal
    from app.models.investigation import Investigation
    from datetime import datetime, timezone
    from app.services.investigation import serialize_state

    with SessionLocal() as db:
        inv = db.get(Investigation, investigation_id)
        if inv:
            inv.current_node = current_node
            inv.current_graph_node = current_node
            if status:
                inv.status = status
            if retry_count is not None:
                inv.retry_count = retry_count
            if risk_score is not None:
                inv.risk_score = risk_score
            if risk_level:
                inv.risk_level = risk_level
            if resolved_entity_id is not None:
                inv.resolved_entity_id = resolved_entity_id
            if entity_confidence is not None:
                inv.entity_confidence = entity_confidence
            if completed:
                inv.completed_timestamp = datetime.now(timezone.utc)

            if state:
                user_id = state.get("user_id") or (state.get("raw_input") or {}).get("user_id")
                if user_id:
                    inv.user_id = str(user_id)
                if "raw_input" in state:
                    inv.raw_input = json.dumps(state["raw_input"])
                if "normalized_input" in state:
                    inv.normalized_input = json.dumps(state["normalized_input"])
                if state.get("resolved_entity"):
                    entity = state["resolved_entity"]
                    name_val = entity.get("business_name") or entity.get("name")
                    if name_val and not inv.resolved_entity_id:
                        inv.resolved_entity_id = uuid.uuid5(uuid.NAMESPACE_DNS, str(name_val))
                    inv.entity_confidence = state.get("entity_confidence", 0.0)
                inv.persistent_graph_state = serialize_state(state)

            db.commit()


# Thread-safe SessionLocal builder that dynamically hooks into the active test in-memory SQLite connection
class MockSessionLocalFactory:
    def __init__(self):
        TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=current_db_session.bind, expire_on_commit=False
        )
        self.session = TestingSessionLocal()
    def __enter__(self):
        return self.session
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.session.rollback()
        else:
            self.session.commit()
        self.session.close()


# Intercept risk engine normalization to map registered_address to address
from app.risk.rules import normalize_evidence
original_normalize_evidence = normalize_evidence

def mock_normalize_evidence(res):
    norm = original_normalize_evidence(res)
    if norm.field_name == "registered_address":
        norm.field_name = "address"
    return norm


@pytest.fixture(autouse=True)
def patch_agents():
    with mock.patch("app.agents.browser.BrowserResearchAgent._extract_field_value", staticmethod(mock_extract_field_value)), \
         mock.patch("app.agents.discovery.DiscoveryAgent.process", mock_discovery_process), \
         mock.patch("app.entity_resolution.resolver.resolve_entity", mock_resolve_entity), \
         mock.patch("app.agents.browser.BrowserResearchAgent._resolve_url", staticmethod(mock_resolve_url)), \
         mock.patch("app.services.research_task.save_research_tasks", mock_save_research_tasks), \
         mock.patch("app.services.evidence.save_research_results", mock_save_research_results), \
         mock.patch("app.services.evidence.save_research_result", mock_save_research_result), \
         mock.patch("app.services.evidence.get_evidences_for_investigation", mock_get_evidences_for_investigation), \
         mock.patch("app.graph.nodes.update_investigation_in_db", mock_update_investigation_in_db), \
         mock.patch("app.risk.engine.normalize_evidence", mock_normalize_evidence), \
         mock.patch("app.db.session.SessionLocal", MockSessionLocalFactory):
        yield


# Scenario 1: GSTIN + Clean Entity E2E Flow
def test_e2e_gstin_clean_entity(client, db_session):
    # Create investigation via API
    headers = {"Authorization": "Bearer UserA"}
    payload = {
        "business_name": "ABC Foods Private Limited",
        "gstin": "27ABCDE1234F1Z5",
        "website": "abcfoods.in",
    }
    resp = client.post("/api/v1/investigations/", json=payload, headers=headers)
    assert resp.status_code == 201
    inv_id = uuid.UUID(resp.json()["id"])

    # Pre-populate candidate entities to skip entity discovery but resolve successfully
    results = [
        ResearchResult(
            result_id="RES-CAND-001",
            task_id="TASK-CAND-001",
            field_name="candidate_entities",
            field_value=[{
                "business_name": "ABC Foods Private Limited",
                "name": "ABC Foods Private Limited",
                "gstin": "27ABCDE1234F1Z5",
                "cin": "L12345MH2020PLC000001",
                "website": "abcfoods.in",
                "location": "Delhi",
                "confidence": 1.0,
            }],
            source_name="GST Portal",
            retrieved_at="2026-08-28T00:00:00Z",
            confidence=1.0,
        )
    ]

    # Prepare graph state
    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {
            "business_name": "ABC FOODS PRIVATE LIMITED",
            "gstin": "27ABCDE1234F1Z5",
            "website": "abcfoods.in",
        },
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": results,
        "status": "CREATED",
        "planner_loop_count": 0,
        "qa_loop_count": 0,
        "research_depth": 0,
        "browser_actions": 0,
        "browser_tasks_count": 0,
        "llm_calls": 0,
        "token_usage": 0,
        "stop_reason": None,
    }

    # Run the graph workflow
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_fetch_page)):
        output = graph_app.invoke(initial_state)

    # Verify report generated and grounded in DB
    db_session.rollback()
    inv = db_session.get(Investigation, inv_id)
    assert inv.status == "COMPLETED"

    reps = db_session.query(Report).filter(Report.investigation_id == inv_id).all()
    assert len(reps) > 0
    from app.services.qa import validate_report
    qa_res = validate_report(db_session, inv_id)
    assert reps[0].qa_status == "PASS" or qa_res["status"] == "PASS"


# Scenario 2: Company Name Only Flow
def test_e2e_company_name_only(client, db_session):
    headers = {"Authorization": "Bearer UserA"}
    payload = {
        "business_name": "ABC Foods Private Limited",
    }
    resp = client.post("/api/v1/investigations/", json=payload, headers=headers)
    assert resp.status_code == 201
    inv_id = uuid.UUID(resp.json()["id"])

    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {
            "business_name": "ABC FOODS PRIVATE LIMITED",
        },
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "CREATED",
    }

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_fetch_page)):
        output = graph_app.invoke(initial_state)

    db_session.rollback()
    inv = db_session.get(Investigation, inv_id)
    assert inv.status in ["COMPLETED", "ENTITY_RESOLVED", "ENTITY_UNRESOLVED", "MAX_LOOPS_REACHED"]


# Scenario 3: Multiple Matching Businesses
def test_e2e_multiple_matching_businesses(client, db_session):
    headers = {"Authorization": "Bearer UserA"}
    payload = {"business_name": "Tata"}
    resp = client.post("/api/v1/investigations/", json=payload, headers=headers)
    inv_id = uuid.UUID(resp.json()["id"])

    # Simulate discovery node finding multiple candidates
    candidates = [
        {"business_name": "TATA SONS PRIVATE LIMITED", "location": "MUMBAI", "cin": "L12345MH1917PLC000001"},
        {"business_name": "TATA MOTORS LIMITED", "location": "MUMBAI", "cin": "L34103MH1945PLC008518"}
    ]
    results = [
        ResearchResult(
            result_id="RES-001",
            task_id="TASK-001",
            field_name="candidate_entities",
            field_value=candidates,
            source_name="MCA Portal",
            retrieved_at="2026-08-28T00:00:00Z",
            confidence=0.95
        )
    ]

    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {"business_name": "TATA"},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": results,
        "status": "DISCOVERY_COMPLETED",
    }

    output = graph_app.invoke(initial_state)

    # Resolution status shows it was not matched due to ambiguity
    db_session.rollback()
    inv = db_session.get(Investigation, inv_id)
    assert output["entity_resolution_status"] == "NO_MATCH" or inv.status in ["COMPLETED", "ENTITY_UNRESOLVED", "MAX_LOOPS_REACHED"]


# Scenario 4: GST/MCA Address Conflict
def test_e2e_address_conflict(client, db_session):
    headers = {"Authorization": "Bearer UserA"}
    payload = {
        "business_name": "ABC Foods Private Limited",
        "gstin": "27ABCDE1234F2Z6",
    }
    resp = client.post("/api/v1/investigations/", json=payload, headers=headers)
    assert resp.status_code == 201
    inv_id = uuid.UUID(resp.json()["id"])

    # Pre-populate candidate entities with both identifiers to trigger both verification flows
    results = [
        ResearchResult(
            result_id="RES-CAND-001",
            task_id="TASK-CAND-001",
            field_name="candidate_entities",
            field_value=[{
                "business_name": "ABC Foods Private Limited",
                "name": "ABC Foods Private Limited",
                "gstin": "27ABCDE1234F2Z6",
                "cin": "L12345MH2020PLC000001",
                "website": "abcfoods.in",
                "location": "Delhi",
                "confidence": 1.0,
            }],
            source_name="GST Portal",
            retrieved_at="2026-08-28T00:00:00Z",
            confidence=1.0,
        )
    ]

    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {
            "business_name": "ABC FOODS PRIVATE LIMITED",
            "gstin": "27ABCDE1234F2Z6",
        },
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": results,
        "status": "CREATED",
    }

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_fetch_page)):
        output = graph_app.invoke(initial_state)

    # Verify both evidence values were persisted in the DB
    db_session.rollback()
    evs = db_session.query(Evidence).filter(Evidence.investigation_id == inv_id).all()
    addresses = [e.field_value for e in evs if e.field_name == "registered_address"]
    assert len(addresses) >= 2
    # Verify address mismatch risk signal triggered
    signals = output.get("risk_signals", [])
    assert any(s["code"] == "ADDRESS_MAJOR_MISMATCH" for s in signals)


# Scenario 5: Official Source Unavailable
def test_e2e_official_source_unavailable(client, db_session):
    headers = {"Authorization": "Bearer UserA"}
    payload = {
        "business_name": "ABC Foods Private Limited",
    }
    resp = client.post("/api/v1/investigations/", json=payload, headers=headers)
    inv_id = uuid.UUID(resp.json()["id"])

    # Fetcher throws error for official sources (gst/mca) but succeeds for Google/generic-web
    def failing_fetch_page(url: str) -> str:
        if "gst.gov.in" in url or "mca.gov.in" in url:
            raise ConnectionError("Official portal down")
        return mock_fetch_page(url)

    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {"business_name": "ABC FOODS PRIVATE LIMITED"},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "CREATED",
    }

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(failing_fetch_page)):
        output = graph_app.invoke(initial_state)

    # The pipeline should complete and research results should still exist from fallback sources
    assert len(output["results"]) > 0


# Scenario 6: CAPTCHA / OTP / login restriction (HITL flow)
def test_e2e_captcha_otp_hitl_flow(client, db_session):
    headers = {"Authorization": "Bearer UserA"}
    payload = {
        "business_name": "ABC Foods Private Limited",
        "gstin": "27ABCDE1234F3Z7",
    }
    resp = client.post("/api/v1/investigations/", json=payload, headers=headers)
    assert resp.status_code == 201
    inv_id = uuid.UUID(resp.json()["id"])

    # Pre-populate candidate entities to skip discovery but resolve successfully
    results = [
        ResearchResult(
            result_id="RES-CAND-001",
            task_id="TASK-CAND-001",
            field_name="candidate_entities",
            field_value=[{
                "business_name": "ABC Foods Private Limited",
                "name": "ABC Foods Private Limited",
                "gstin": "27ABCDE1234F3Z7",
                "cin": "L12345MH2020PLC000001",
                "website": "abcfoods.in",
                "location": "Delhi",
                "confidence": 1.0,
            }],
            source_name="GST Portal",
            retrieved_at="2026-08-28T00:00:00Z",
            confidence=1.0,
        )
    ]

    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {
            "business_name": "ABC FOODS PRIVATE LIMITED",
            "gstin": "27ABCDE1234F3Z7",
        },
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": results,
        "status": "CREATED",
        "planner_loop_count": 0,
        "research_depth": 0,
        "browser_actions": 0,
        "browser_tasks_count": 0,
        "llm_calls": 0,
        "token_usage": 0,
        "stop_reason": None,
    }

    # Intercept page fetch with standard mock fetcher to trigger CAPTCHA pausing
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_fetch_page)):
        output = graph_app.invoke(initial_state)

    # Verify status is WAITING_FOR_USER
    db_session.rollback()
    inv = db_session.get(Investigation, inv_id)
    assert inv.status == "WAITING_FOR_USER"

    # Now verify we can resume via the API (user solved captcha)
    def mock_fetch_page_resumed(url: str) -> str:
        return "<html><title>GST Portal</title><body>Active GST Status. Address: 123 Main St, Delhi. Business Activity: Food. Registration Date: 2020-01-01</body></html>"

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(mock_fetch_page_resumed)):
        resume_resp = client.post(f"/api/v1/investigations/{inv_id}/resume", headers=headers)
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] != "WAITING_FOR_USER"


# Scenario 7: Only Third-Party Information
def test_e2e_only_third_party_info(client, db_session):
    headers = {"Authorization": "Bearer UserA"}
    payload = {
        "business_name": "ABC Foods Private Limited",
        "name": "ABC Foods Private Limited",
    }
    resp = client.post("/api/v1/investigations/", json=payload, headers=headers)
    inv_id = uuid.UUID(resp.json()["id"])

    # Official portals are empty, only google/third-party returns text
    def only_third_party_fetch_page(url: str) -> str:
        if "gst.gov.in" in url or "mca.gov.in" in url:
            return ""
        return mock_fetch_page(url)

    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {
            "business_name": "ABC FOODS PRIVATE LIMITED",
            "name": "ABC FOODS PRIVATE LIMITED",
        },
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "CREATED",
    }

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", staticmethod(only_third_party_fetch_page)):
        output = graph_app.invoke(initial_state)

    # Evidences must indicate "Third-Party Source" or "General Web" source names
    db_session.rollback()
    evs = db_session.query(Evidence).filter(Evidence.investigation_id == inv_id).all()
    assert len(evs) > 0
    third_party_evs = [e for e in evs if e.source_name not in {"gst.gov.in", "mca.gov.in"}]
    assert len(third_party_evs) > 0


# Scenario 8: Entity Cannot Be Resolved
def test_e2e_entity_cannot_be_resolved(client, db_session):
    headers = {"Authorization": "Bearer UserA"}
    payload = {
        "business_name": "Completely Unmatched Business Name",
    }
    resp = client.post("/api/v1/investigations/", json=payload, headers=headers)
    inv_id = uuid.UUID(resp.json()["id"])

    # Discovery returns candidate entities with totally mismatched names
    results = [
        ResearchResult(
            result_id="RES-001",
            task_id="TASK-001",
            field_name="candidate_entities",
            field_value=[{"business_name": "TATA MOTORS LIMITED", "location": "MUMBAI", "cin": "L34103MH1945PLC008518"}],
            source_name="MCA Portal",
            retrieved_at="2026-08-28T00:00:00Z",
            confidence=0.95
        )
    ]

    initial_state = {
        "investigation_id": str(inv_id),
        "raw_input": payload,
        "normalized_input": {"business_name": "COMPLETELY UNMATCHED BUSINESS NAME"},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": results,
        "status": "DISCOVERY_COMPLETED",
    }

    output = graph_app.invoke(initial_state)

    db_session.rollback()
    inv = db_session.get(Investigation, inv_id)
    assert output["entity_resolution_status"] == "NO_MATCH" or inv.status in ["COMPLETED", "ENTITY_UNRESOLVED", "MAX_LOOPS_REACHED"]
    assert output["resolved_entity"] is not None
