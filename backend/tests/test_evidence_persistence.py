import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.graph.state import ResearchResult
from app.main import app
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.services.evidence import (
    get_evidences_for_investigation,
    save_research_result,
    save_research_results,
)


@pytest.fixture(name="db_session")
def fixture_db_session():
    # Use SQLite in memory for testing with StaticPool to share connection across threads
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


def make_test_result(**overrides):
    data = {
        "result_id": "RES-001",
        "task_id": "TASK-001",
        "field_name": "gst_status",
        "field_value": "Active",
        "source_name": "GST Portal",
        "source_url": "https://www.gst.gov.in",
        "retrieved_at": "2026-08-26T10:00:00+00:00",
        "confidence": 0.95,
    }
    data.update(overrides)
    return ResearchResult(**data)


def test_valid_research_result_is_persisted(db_session, investigation_id):
    result = make_test_result()
    evidence = save_research_result(db_session, result, investigation_id)

    assert evidence is not None
    assert evidence.research_result_id == "RES-001"
    assert evidence.task_id == "TASK-001"
    assert evidence.field_name == "gst_status"
    assert evidence.field_value == "Active"

    # Check that it's actually in the database and retrieved values match
    db_evidence = db_session.get(Evidence, evidence.id)
    assert db_evidence is not None
    assert db_evidence.research_result_id == "RES-001"
    assert db_evidence.task_id == "TASK-001"
    assert db_evidence.field_name == "gst_status"
    assert db_evidence.field_value == "Active"


def test_invalid_research_result_is_not_persisted(
    db_session, investigation_id
):
    # Invalid confidence (1.5 is > 1.0)
    result = make_test_result(confidence=1.5)
    evidence = save_research_result(db_session, result, investigation_id)

    assert evidence is None

    # Query database to confirm nothing was saved
    count = db_session.query(Evidence).count()
    assert count == 0


def test_persisted_evidence_retains_source_metadata(
    db_session, investigation_id
):
    result = make_test_result(
        source_name="MCA Portal",
        source_url="https://www.mca.gov.in/company-search",
    )
    evidence = save_research_result(db_session, result, investigation_id)

    assert evidence is not None
    assert evidence.source_name == "MCA Portal"
    assert evidence.source_url == "https://www.mca.gov.in/company-search"

    # Retrieved timestamp check
    assert evidence.retrieved_timestamp is not None
    # We parsed "2026-08-26T10:00:00+00:00"
    assert evidence.retrieved_timestamp.year == 2026


def test_persisted_evidence_retains_confidence(db_session, investigation_id):
    result = make_test_result(confidence=0.88)
    evidence = save_research_result(db_session, result, investigation_id)

    assert evidence is not None
    assert evidence.confidence == 0.88


def test_persisted_evidence_is_associated_with_correct_investigation(
    db_session, investigation_id
):
    result = make_test_result()
    evidence = save_research_result(db_session, result, investigation_id)

    assert evidence is not None
    assert evidence.investigation_id == investigation_id


def test_evidence_can_be_retrieved_for_an_investigation(
    db_session, investigation_id
):
    result1 = make_test_result(
        result_id="RES-001", field_name="gst_status", field_value="Active"
    )
    result2 = make_test_result(
        result_id="RES-002",
        field_name="legal_name",
        field_value="Acme Corp",
    )

    save_research_result(db_session, result1, investigation_id)
    save_research_result(db_session, result2, investigation_id)

    # Retrieve
    evidences = get_evidences_for_investigation(db_session, investigation_id)
    assert len(evidences) == 2

    result_ids = {ev.research_result_id for ev in evidences}
    assert result_ids == {"RES-001", "RES-002"}


def test_evidence_does_not_leak_between_investigations(db_session):
    inv_a = Investigation(input_data='{"business_name": "Company A"}')
    inv_b = Investigation(input_data='{"business_name": "Company B"}')
    db_session.add_all([inv_a, inv_b])
    db_session.commit()
    db_session.refresh(inv_a)
    db_session.refresh(inv_b)

    result_a = make_test_result(result_id="RES-A", field_value="Value A")
    result_b = make_test_result(result_id="RES-B", field_value="Value B")

    save_research_result(db_session, result_a, inv_a.id)
    save_research_result(db_session, result_b, inv_b.id)

    # Retrieve for A and assert no leakage from B
    evidences_a = get_evidences_for_investigation(db_session, inv_a.id)
    assert len(evidences_a) == 1
    assert evidences_a[0].research_result_id == "RES-A"
    assert evidences_a[0].field_value == "Value A"

    # Retrieve for B and assert no leakage from A
    evidences_b = get_evidences_for_investigation(db_session, inv_b.id)
    assert len(evidences_b) == 1
    assert evidences_b[0].research_result_id == "RES-B"
    assert evidences_b[0].field_value == "Value B"


def test_graph_integration_persists_evidence(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    initial_state = {
        "investigation_id": str(investigation_id),
        "raw_input": {
            "business_name": "ABC Foods Pvt Ltd",
            "gstin": "27abcde1234f1z5",
            "website": "abcfoods.in",
            "location": "Noida",
        },
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 0,
        "status": "CREATED",
    }

    # Patch SessionLocal to return our test db_session context manager
    class MockSessionLocal:

        def __enter__(self):
            return db_session

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("app.db.session.SessionLocal", MockSessionLocal):
        output_state = graph_app.invoke(initial_state)

    # Check that evidences were persisted in the database!
    evidences = get_evidences_for_investigation(db_session, investigation_id)
    assert len(evidences) > 0

    field_names = {ev.field_name for ev in evidences}
    assert "candidate_entities" in field_names


def test_graph_execution_with_non_uuid_does_not_break(db_session):
    from app.graph.workflow import app as graph_app

    initial_state = {
        "investigation_id": "NON-UUID-STRING-MOCK",
        "raw_input": {
            "business_name": "ABC Foods Pvt Ltd",
            "gstin": "27abcde1234f1z5",
            "website": "abcfoods.in",
            "location": "Noida",
        },
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 0,
        "status": "CREATED",
    }

    # Patch SessionLocal to return our test db_session context manager
    class MockSessionLocal:

        def __enter__(self):
            return db_session

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("app.db.session.SessionLocal", MockSessionLocal):
        output_state = graph_app.invoke(initial_state)

    assert output_state is not None
    # No evidence should be persisted as investigation_id is not a valid UUID
    count = db_session.query(Evidence).count()
    assert count == 0


def test_get_investigation_evidence_api(db_session, investigation_id):
    # Setup mock dependency for get_db
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    # Save some test evidence
    result = make_test_result(
        result_id="RES-003", field_name="gst_status", field_value="Active", confidence=0.85
    )
    save_research_result(db_session, result, investigation_id)

    client = TestClient(app)
    response = client.get(f"/api/v1/investigations/{investigation_id}/evidence")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["research_result_id"] == "RES-003"
    assert data[0]["field_value"] == "Active"

    # Assert missing field confidence is returned correctly
    assert "confidence" in data[0]
    assert data[0]["confidence"] == 0.85

    # Clean up overrides
    app.dependency_overrides.clear()


def test_get_investigation_evidence_api_not_found(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    non_existent_id = uuid.uuid4()
    response = client.get(
        f"/api/v1/investigations/{non_existent_id}/evidence"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Investigation not found."

    app.dependency_overrides.clear()


def test_timezone_aware_timestamp_persistence_and_retrieval(db_session, investigation_id):
    # Retrieve timestamp with explicit non-UTC offset
    result = make_test_result(
        result_id="RES-TZ-1",
        retrieved_at="2026-08-26T12:34:56+05:30",
    )
    evidence = save_research_result(db_session, result, investigation_id)
    assert evidence is not None

    # 12:34:56+05:30 represents 07:04:56 UTC.
    dt = evidence.retrieved_timestamp
    if dt.tzinfo is not None:
        dt_utc = dt.astimezone(timezone.utc)
    else:
        dt_utc = dt.replace(tzinfo=timezone.utc)

    assert dt_utc.hour == 7
    assert dt_utc.minute == 4

    # Run API test
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    response = client.get(f"/api/v1/investigations/{investigation_id}/evidence")
    assert response.status_code == 200
    data = response.json()

    # Verify offset is preserved in API output (should represent the correct point in time in ISO 8601)
    api_timestamp = data[0]["retrieved_timestamp"]
    assert "07:04:56" in api_timestamp
    assert "+00:00" in api_timestamp or "Z" in api_timestamp

    app.dependency_overrides.clear()


def test_save_research_results_batch_transaction(db_session, investigation_id):
    results = [
        make_test_result(result_id="BATCH-1", field_name="f1", field_value="v1"),
        make_test_result(result_id="BATCH-2", field_name="f2", field_value="v2"),
    ]

    # Verify batch function works and inserts all items
    evs = save_research_results(db_session, results, investigation_id)
    assert len(evs) == 2
    assert evs[0].research_result_id == "BATCH-1"
    assert evs[1].research_result_id == "BATCH-2"

    # Check database query
    db_evs = get_evidences_for_investigation(db_session, investigation_id)
    assert len(db_evs) == 2


def test_trailing_z_timestamp_parsing(db_session, investigation_id):
    # Retrieve timestamp ending with 'Z'
    result = make_test_result(
        result_id="RES-Z-1",
        retrieved_at="2026-08-26T10:00:00Z",
    )
    evidence = save_research_result(db_session, result, investigation_id)
    assert evidence is not None

    # 10:00:00Z represents 10:00:00 UTC.
    dt = evidence.retrieved_timestamp
    if dt.tzinfo is not None:
        dt_utc = dt.astimezone(timezone.utc)
    else:
        dt_utc = dt.replace(tzinfo=timezone.utc)

    assert dt_utc.hour == 10
    assert dt_utc.minute == 0
