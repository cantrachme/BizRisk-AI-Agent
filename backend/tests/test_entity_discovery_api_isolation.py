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

def test_entity_discovery_api_valid_input(client_override):
    # Valid input with GSTIN/CIN (confidence 0.95)
    payload = {
        "business_name": "Wipro Limited",
        "gstin": "27AAACW0387R1Z6",
        "cin": "L32102KA1945PLC020800",
        "website": "www.wipro.com",
        "location": "Bengaluru, Karnataka, India"
    }
    resp = client_override.post("/api/v1/test/entity-discovery", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["candidate_entities"]) == 1
    cand = data["candidate_entities"][0]
    assert cand["name"] == "Wipro Limited"
    assert cand["gstin"] == "27AAACW0387R1Z6"
    assert cand["cin"] == "L32102KA1945PLC020800"
    assert cand["website"] == "www.wipro.com"
    assert cand["location"] == "Bengaluru, Karnataka, India"
    assert cand["confidence"] == 0.95

def test_entity_discovery_api_different_inputs(client_override):
    # Name + Website + Location (confidence 0.80)
    payload = {
        "business_name": "Wipro Limited",
        "website": "www.wipro.com",
        "location": "Bengaluru, Karnataka, India"
    }
    resp = client_override.post("/api/v1/test/entity-discovery", json=payload)
    data = resp.json()
    assert data["candidate_entities"][0]["confidence"] == 0.80

    # Name + (Website or Location) (confidence 0.70)
    payload = {
        "business_name": "Wipro Limited",
        "website": "www.wipro.com"
    }
    resp = client_override.post("/api/v1/test/entity-discovery", json=payload)
    data = resp.json()
    assert data["candidate_entities"][0]["confidence"] == 0.70

    # Name only (confidence 0.50)
    payload = {
        "business_name": "Wipro Limited"
    }
    resp = client_override.post("/api/v1/test/entity-discovery", json=payload)
    data = resp.json()
    assert data["candidate_entities"][0]["confidence"] == 0.50

def test_entity_discovery_api_empty_input(client_override):
    payload = {}
    resp = client_override.post("/api/v1/test/entity-discovery", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["candidate_entities"] == []

def test_entity_discovery_api_isolation(client_override, db_session):
    # Verify isolation: no investigations are created
    initial_count = db_session.query(Investigation).count()
    
    payload = {
        "business_name": "Wipro Limited",
        "gstin": "27AAACW0387R1Z6"
    }
    resp = client_override.post("/api/v1/test/entity-discovery", json=payload)
    assert resp.status_code == 200
    
    final_count = db_session.query(Investigation).count()
    assert final_count == initial_count
