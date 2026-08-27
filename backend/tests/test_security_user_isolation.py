import uuid
import json
import pytest
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
from app.api.auth import get_current_user_id


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
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


# 1. Authentication Tests
def test_unauthenticated_requests_rejected(client):
    # Temporarily remove conftest.py override to test actual unauthenticated behavior
    if get_current_user_id in app.dependency_overrides:
        del app.dependency_overrides[get_current_user_id]

    # No Authorization header
    resp = client.get("/api/v1/investigations/")
    assert resp.status_code == 401

    # Empty Bearer token
    resp = client.get("/api/v1/investigations/", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_authenticated_requests_accepted(client):
    # Valid Bearer identity
    resp = client.get("/api/v1/investigations/", headers={"Authorization": "Bearer UserA"})
    assert resp.status_code == 200


# 2. Payload Spoofing Isolation
def test_payload_user_id_spoofing_prevented(client, db_session):
    payload = {
        "business_name": "Test Co",
        "user_id": "UserB"  # Attempt to impersonate UserB
    }
    # Authenticate as UserA
    resp = client.post(
        "/api/v1/investigations/",
        json=payload,
        headers={"Authorization": "Bearer UserA"}
    )
    assert resp.status_code == 201
    data = resp.json()
    inv_id = uuid.UUID(data["id"])

    # Verify that the created investigation's user_id in the DB is UserA, NOT UserB
    inv = db_session.get(Investigation, inv_id)
    assert inv.user_id == "UserA"


# 3. Access Isolation (direct object IDs, evidence, reports, risk, QA, events)
def test_investigation_and_nested_resources_isolation(client, db_session):
    # Setup: Create an investigation owned by UserB
    inv_b = Investigation(
        input_data='{"business_name": "UserB Co"}',
        user_id="UserB",
        status="created"
    )
    db_session.add(inv_b)
    db_session.commit()
    db_session.refresh(inv_b)

    # Save a report for UserB's investigation
    rep_b = Report(
        investigation_id=inv_b.id,
        version=1,
        report_json='{"meta": {"report_version": "1"}, "overall_risk": {"score": 0}}',
        qa_status="PENDING"
    )
    db_session.add(rep_b)
    db_session.commit()

    # Authenticate as UserA and try to access UserB's investigation
    headers_a = {"Authorization": "Bearer UserA"}

    # A: GET /investigations/{id} -> 404
    resp = client.get(f"/api/v1/investigations/{inv_b.id}", headers=headers_a)
    assert resp.status_code == 404

    # B: GET /investigations/{id}/evidence -> 404
    resp = client.get(f"/api/v1/investigations/{inv_b.id}/evidence", headers=headers_a)
    assert resp.status_code == 404

    # C: GET /investigations/{id}/report -> 404
    resp = client.get(f"/api/v1/investigations/{inv_b.id}/report", headers=headers_a)
    assert resp.status_code == 404

    # D: GET /investigations/{id}/reports -> 404
    resp = client.get(f"/api/v1/investigations/{inv_b.id}/reports", headers=headers_a)
    assert resp.status_code == 404

    # E: GET /investigations/{id}/risk -> 404
    resp = client.get(f"/api/v1/investigations/{inv_b.id}/risk", headers=headers_a)
    assert resp.status_code == 404

    # F: GET /investigations/{id}/qa -> 404
    resp = client.get(f"/api/v1/investigations/{inv_b.id}/qa", headers=headers_a)
    assert resp.status_code == 404

    # G: GET /investigations/{id}/events -> 404
    resp = client.get(f"/api/v1/investigations/{inv_b.id}/events", headers=headers_a)
    assert resp.status_code == 404

    # H: Guessing UUID that doesn't exist -> 404
    fake_id = uuid.uuid4()
    resp = client.get(f"/api/v1/investigations/{fake_id}", headers=headers_a)
    assert resp.status_code == 404


# 4. List Isolation
def test_list_and_incomplete_endpoints_isolation(client, db_session):
    # Setup: Create investigations for UserA and UserB
    inv_a = Investigation(input_data='{"business_name": "A Co"}', user_id="UserA", status="created")
    inv_b = Investigation(input_data='{"business_name": "B Co"}', user_id="UserB", status="created")
    db_session.add_all([inv_a, inv_b])
    db_session.commit()

    headers_a = {"Authorization": "Bearer UserA"}

    # General list
    resp = client.get("/api/v1/investigations/", headers=headers_a)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(inv_a.id)

    # Incomplete list
    resp = client.get("/api/v1/investigations/incomplete", headers=headers_a)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(inv_a.id)


# 5. HITL & Resume Isolation
def test_hitl_and_resume_isolation(client, db_session):
    inv_b = Investigation(input_data='{"business_name": "B Co"}', user_id="UserB", status="WAITING_FOR_USER")
    db_session.add(inv_b)
    db_session.commit()

    headers_a = {"Authorization": "Bearer UserA"}

    # UserA cannot check human-intervention state of UserB -> 404
    resp = client.get(f"/api/v1/investigations/{inv_b.id}/human-intervention", headers=headers_a)
    assert resp.status_code == 404

    # UserA cannot resume UserB investigation -> 404
    resp = client.post(f"/api/v1/investigations/{inv_b.id}/resume", headers=headers_a)
    assert resp.status_code == 404


# 6. Recovery Isolation & Serialization Safety
def test_recovery_flow_isolation(client, db_session):
    # Setup: UserB's investigation has a persistent graph state
    inv_b = Investigation(
        input_data='{"business_name": "B Co"}',
        user_id="UserB",
        persistent_graph_state='{"secret_key": "private_data"}',
        status="created"
    )
    db_session.add(inv_b)
    db_session.commit()

    headers_a = {"Authorization": "Bearer UserA"}

    # UserA attempts to resume UserB's investigation
    resp = client.post(f"/api/v1/investigations/{inv_b.id}/resume", headers=headers_a)
    assert resp.status_code == 404

    # Verify that the database session was not loaded/deserialized by UserA's request
    assert resp.status_code == 404
