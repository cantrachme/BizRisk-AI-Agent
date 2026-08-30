import pytest
import uuid
from unittest import mock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base
from app.main import app as fastapi_app
from app.models.investigation import Investigation
import sys
from backend.tests.conftest import mock_background_workflow
original_run_workflow = getattr(sys, "_original_run_investigation_workflow", None)


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


def test_create_investigation_triggers_background_task(client):
    headers = {"Authorization": "Bearer TestUserBackground"}
    payload = {
        "business_name": "TCS Global Limited",
        "website": "tcs.com"
    }

    # Verify background task is triggered
    response = client.post("/api/v1/investigations/", json=payload, headers=headers)
    assert response.status_code == 201
    
    # Assert background task was added
    mock_background_workflow.assert_called_once()
    args, kwargs = mock_background_workflow.call_args
    assert len(args) == 1
    # Check that it was called with the created investigation ID
    assert str(args[0]) == response.json()["id"]


def test_run_investigation_workflow_sets_pending_and_invokes_graph(db_session):
    # 1. Create a dummy investigation in the created state
    inv = Investigation(
        input_data='{"business_name": "Test Company Mock"}',
        user_id="user-123",
        status="created"
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Mock the compiled graph invocation and database session
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.graph.workflow.app.invoke") as mock_invoke:
        # Run background workflow task directly (using unmocked original function)
        original_run_workflow(inv.id)
        
        # Verify db status transitioned to PENDING
        db_session.rollback()
        db_refreshed = db_session.get(Investigation, inv.id)
        assert db_refreshed.status == "PENDING"
        
        # Verify invoke was called
        mock_invoke.assert_called_once()


def test_run_investigation_workflow_prevents_duplicate_execution(db_session):
    # Create investigation already in PENDING status
    inv = Investigation(
        input_data='{"business_name": "Test Company Mock"}',
        user_id="user-123",
        status="PENDING"
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.graph.workflow.app.invoke") as mock_invoke:
        original_run_workflow(inv.id)
        # Should return immediately and NOT invoke graph
        mock_invoke.assert_not_called()


def test_run_investigation_workflow_exception_sets_failed(db_session):
    inv = Investigation(
        input_data='{"business_name": "Test Company Mock"}',
        user_id="user-123",
        status="created"
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Force invoke to raise an exception
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.graph.workflow.app.invoke", side_effect=Exception("Simulated Error")):
        original_run_workflow(inv.id)
        
        # Verify status transitioned to FAILED
        db_session.rollback()
        db_refreshed = db_session.get(Investigation, inv.id)
        assert db_refreshed.status == "FAILED"


def test_resume_investigation_prevents_duplicate_active_execution(client, db_session):
    headers = {"Authorization": "Bearer TestUserBackground"}
    # Create active investigation
    inv = Investigation(
        input_data='{"business_name": "Test Company Mock"}',
        user_id="TestUserBackground",
        status="RUNNING"
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)

    # Try to resume running case -> should fail with 400
    resp = client.post(f"/api/v1/investigations/{inv.id}/resume", headers=headers)
    assert resp.status_code == 400
    assert "Cannot resume investigation" in resp.json()["detail"]
