import pytest
import uuid
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

def test_planner_api_valid_normalized_input(client_override):
    # Test 1: Full normalized company input
    payload = {
        "normalized_input": {
            "business_name": "WIPRO LIMITED",
            "gstin": "27AAACW0387R1Z6",
            "cin": "L32102KA1945PLC020800",
            "website": "https://www.wipro.com",
            "location": "Bengaluru, Karnataka, India"
        }
    }
    resp = client_override.post("/api/v1/test/planner", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    new_tasks = data["new_tasks"]
    task_types = {t["task_type"] for t in new_tasks}
    assert "GST_VERIFICATION" in task_types
    assert "MCA_VERIFICATION" in task_types
    assert "WEBSITE_VERIFICATION" in task_types
    assert "ENTITY_DISCOVERY" not in task_types

def test_planner_api_limited_identifiers(client_override):
    # Test 2: Business name + website/location without GSTIN/CIN
    payload = {
        "normalized_input": {
            "business_name": "Wipro Limited",
            "website": "https://www.wipro.com",
            "location": "Bengaluru, Karnataka, India"
        }
    }
    resp = client_override.post("/api/v1/test/planner", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    new_tasks = data["new_tasks"]
    task_types = {t["task_type"] for t in new_tasks}
    assert "ENTITY_DISCOVERY" in task_types
    assert "WEBSITE_VERIFICATION" in task_types

def test_planner_api_empty_input(client_override):
    # Test 3: No input
    payload = {}
    resp = client_override.post("/api/v1/test/planner", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["new_tasks"] == []

def test_planner_api_isolation(client_override, db_session):
    # F. Verify isolation
    initial_count = db_session.query(Investigation).count()
    
    payload = {
        "normalized_input": {
            "business_name": "Wipro Limited",
            "gstin": "27AAACW0387R1Z6"
        }
    }
    resp = client_override.post("/api/v1/test/planner", json=payload)
    assert resp.status_code == 200
    
    final_count = db_session.query(Investigation).count()
    assert final_count == initial_count
