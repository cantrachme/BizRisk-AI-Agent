import pytest
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db.base import Base
from app.main import app as fastapi_app
from app.models.investigation import Investigation

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
    client = TestClient(fastapi_app)
    yield client
    fastapi_app.dependency_overrides.clear()

def test_intake_api_valid_input(client):
    # A. Valid input:
    # - business name is uppercased
    # - valid GSTIN is preserved
    # - valid CIN is preserved
    # - whitespace is normalized
    # - website gets https://
    payload = {
        "business_name": "  Wipro   Limited  ",
        "gstin": "27AAACW0387R1Z6",
        "cin": "L32102KA1945PLC020800",
        "epfo_code": "  EPFO123  ",
        "website": "www.wipro.com",
        "location": "  Bengaluru, Karnataka, India  ",
        "people": ["John Doe"]
    }
    resp = client.post("/api/v1/test/intake", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["business_name"] == "WIPRO LIMITED"
    assert data["gstin"] == "27AAACW0387R1Z6"
    assert data["cin"] == "L32102KA1945PLC020800"
    assert data["epfo_code"] == "EPFO123"
    assert data["website"] == "https://www.wipro.com"
    assert data["location"] == "Bengaluru, Karnataka, India"
    assert data["people"] == ["John Doe"]

def test_intake_api_invalid_gstin(client):
    # B. Invalid GSTIN
    # Expected: "gstin": null
    payload = {
        "gstin": "INVALIDGSTIN123"
    }
    resp = client.post("/api/v1/test/intake", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["gstin"] is None

def test_intake_api_invalid_cin(client):
    # C. Invalid CIN
    # Expected: "cin": null
    payload = {
        "cin": "INVALIDCIN123"
    }
    resp = client.post("/api/v1/test/intake", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["cin"] is None

def test_intake_api_invalid_website(client):
    # D. Invalid website
    # Expected: "website": null
    payload = {
        "website": "ftp://invalid-website"
    }
    resp = client.post("/api/v1/test/intake", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["website"] is None

def test_intake_api_empty_missing_fields(client):
    # E. Empty/missing fields
    # Expected nullable fields become null and people defaults to [].
    payload = {}
    resp = client.post("/api/v1/test/intake", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["business_name"] is None
    assert data["gstin"] is None
    assert data["cin"] is None
    assert data["epfo_code"] is None
    assert data["website"] is None
    assert data["location"] is None
    assert data["people"] == []

def test_intake_api_isolation(client, db_session):
    # F. Verify isolation
    # The endpoint must not create an investigation or invoke downstream agents.
    initial_count = db_session.query(Investigation).count()
    
    payload = {
        "business_name": "Wipro Limited",
        "gstin": "27AAACW0387R1Z6",
        "cin": "L32102KA1945PLC020800",
        "epfo_code": "EPFO123",
        "website": "www.wipro.com",
        "location": "Bengaluru, Karnataka, India",
        "people": []
    }
    resp = client.post("/api/v1/test/intake", json=payload)
    assert resp.status_code == 200
    
    final_count = db_session.query(Investigation).count()
    assert final_count == initial_count


def test_intake_api_not_a_valid_url(client):
    payload = {
        "website": "not-a-valid-url"
    }
    resp = client.post("/api/v1/test/intake", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["website"] is None

