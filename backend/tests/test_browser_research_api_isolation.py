import pytest
import uuid
from unittest import mock
from fastapi.testclient import TestClient
from app.main import app as fastapi_app
from app.models.investigation import Investigation

client = TestClient(fastapi_app)

@pytest.fixture(name="db_session")
def fixture_db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
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

@pytest.fixture(name="client_override")
def fixture_client_override(db_session):
    from app.db import get_db
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    fastapi_app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(fastapi_app)
    yield test_client
    fastapi_app.dependency_overrides.clear()

def test_browser_api_successful_gst_verification(client_override):
    # Test 1: Successful GST Verification
    mock_html = """
    <html>
    <head><title>WIPRO LIMITED</title></head>
    <body>
      <div>GSTIN: 27AAACW0387R1Z6</div>
      <div>Legal Name: WIPRO LIMITED</div>
      <div>GST status: Active</div>
      <div>Registered Address: 74/2, Doddakannelli, Sarjapur Road, Bengaluru 560035, Karnataka, India</div>
      <div>business_activity: IT Services</div>
    </body>
    </html>
    """
    payload = {
        "task_id": "TASK-001",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name", "gst_status", "registered_address"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    assert len(data["results"]) == 3
    
    fields = {r["field_name"]: r for r in data["results"]}
    assert fields["legal_name"]["field_value"] == "WIPRO LIMITED"
    assert fields["gst_status"]["field_value"] == "AVAILABLE"
    assert "Sarjapur Road" in fields["registered_address"]["field_value"]

def test_browser_api_generic_homepage_not_found(client_override):
    # Test 2: Generic page with missing requested fields returns NOT_FOUND
    mock_html = """
    <html>
    <head><title>Welcome</title></head>
    <body>
      Welcome to our landing page. We offer outstanding customer support.
    </body>
    </html>
    """
    payload = {
        "task_id": "TASK-002",
        "task_type": "WEBSITE_VERIFICATION",
        "target": "https://example.com",
        "objective": "Verify website details",
        "required_fields": ["registered_address", "established_year"],
        "priority": 2,
        "preferred_sources": ["company_website"]
    }
    
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    
    for res in data["results"]:
        assert res["field_value"] == "NOT_FOUND"
        assert res["confidence"] == 0.0

def test_browser_api_error_page_classification(client_override):
    # Test 3: Browser error pages are not treated as valid evidence
    mock_html = """
    <html>
    <body>
      403 Forbidden Access Denied
    </body>
    </html>
    """
    payload = {
        "task_id": "TASK-003",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["registered_address"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0

def test_browser_api_human_intervention(client_override):
    # Test 4: CAPTCHA handling returns unverified results with confidence 0
    mock_html = """
    <html>
    <body>
      Please solve the captcha to proceed.
    </body>
    </html>
    """
    payload = {
        "task_id": "TASK-004",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0

def test_browser_api_isolation(client_override, db_session):
    # Test 5: Verify no investigation/evidence record is written to the database
    initial_count = db_session.query(Investigation).count()
    
    mock_html = "<html><head><title>WIPRO LIMITED</title></head><body>Legal Name: WIPRO LIMITED</body></html>"
    payload = {
        "task_id": "TASK-005",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    final_count = db_session.query(Investigation).count()
    assert final_count == initial_count
