import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.investigation import Investigation
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


# 1. CORS OPTIONS / Preflight Tests
def test_cors_options_preflight_investigations(client):
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization, Content-Type",
    }
    
    # Preflight for /investigations/
    resp = client.options("/api/v1/investigations/", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert resp.headers.get("access-control-allow-credentials") == "true"
    allow_headers = resp.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allow_headers
    assert "content-type" in allow_headers

    # Preflight for /investigations/incomplete
    resp_inc = client.options("/api/v1/investigations/incomplete", headers=headers)
    assert resp_inc.status_code == 200
    assert resp_inc.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert resp_inc.headers.get("access-control-allow-credentials") == "true"


# 2. CORS Headers on Actual Request
def test_cors_headers_on_actual_request(client, db_session):
    # Ensure any conftest dependency overrides for auth are cleared to test bearer header directly
    if get_current_user_id in app.dependency_overrides:
        del app.dependency_overrides[get_current_user_id]

    headers = {
        "Origin": "http://localhost:3000",
        "Authorization": "Bearer UserA",
    }

    # Add an investigation owned by UserA
    inv = Investigation(
        id=uuid.uuid4(),
        user_id="UserA",
        status="CREATED",
        input_data="{}"
    )
    db_session.add(inv)
    db_session.commit()

    resp = client.get("/api/v1/investigations/", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert len(resp.json()) == 1


# 3. Unauthenticated requests still fail with 401
def test_unauthenticated_requests_still_fail(client):
    if get_current_user_id in app.dependency_overrides:
        del app.dependency_overrides[get_current_user_id]

    resp = client.get("/api/v1/investigations/")
    assert resp.status_code == 401


# 4. User Isolation remains unchanged
def test_cors_user_isolation(client, db_session):
    if get_current_user_id in app.dependency_overrides:
        del app.dependency_overrides[get_current_user_id]

    # Create investigation for UserB
    inv_b = Investigation(
        id=uuid.uuid4(),
        user_id="UserB",
        status="CREATED",
        input_data="{}"
    )
    db_session.add(inv_b)
    db_session.commit()

    # UserA requests investigations list - should get empty list
    headers_a = {
        "Origin": "http://localhost:3000",
        "Authorization": "Bearer UserA",
    }
    resp = client.get("/api/v1/investigations/", headers=headers_a)
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    # UserA tries to access UserB's investigation detail directly
    resp_detail = client.get(f"/api/v1/investigations/{inv_b.id}", headers=headers_a)
    assert resp_detail.status_code == 404
