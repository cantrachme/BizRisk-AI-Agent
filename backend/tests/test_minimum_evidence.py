import uuid
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
from app.models.investigation import Investigation
from app.models.evidence import Evidence
from app.risk.engine import calculate_risk_analysis, InsufficientEvidenceError


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


@pytest.fixture(name="investigation_id")
def fixture_investigation_id(db_session):
    inv = Investigation(input_data='{"business_name": "Test Company"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


def make_test_result(result_id, source_name, source_url=None):
    return ResearchResult(
        result_id=result_id,
        task_id="TASK-001",
        field_name="gst_status",
        field_value="Active",
        source_name=source_name,
        source_url=source_url or "https://mock.com",
        retrieved_at="2026-08-26T10:00:00+00:00",
        confidence=0.95,
    )


# Active policy config mock
POLICY_ENABLED_CONFIG = {
    "rules": {},
    "risk_levels": {},
    "minimum_evidence_policy": {
        "enabled": True,
        "min_legal_identity_sources": 1,
        "min_supporting_sources": 1,
        "legal_identity_sources": ["GST Portal", "MCA Portal"],
        "supporting_sources": ["Company Website", "General Web", "Third-Party Source"]
    }
}


# 1. Verify scoring succeeds if the policy is met (1 legal source + 1 supporting source)
@patch("app.risk.engine.load_config", return_value=POLICY_ENABLED_CONFIG)
def test_policy_met_succeeds(mock_load):
    results = [
        make_test_result("R1", "GST Portal"),
        make_test_result("R2", "Company Website"),
    ]
    analysis = calculate_risk_analysis(results)
    assert "overall_risk" in analysis


# 2. Verify scoring raises InsufficientEvidenceError if no legal source is present
@patch("app.risk.engine.load_config", return_value=POLICY_ENABLED_CONFIG)
def test_missing_legal_source_raises_error(mock_load):
    results = [
        make_test_result("R1", "Company Website"),
        make_test_result("R2", "General Web"),
    ]
    with pytest.raises(InsufficientEvidenceError, match="Required: 1 legal source"):
        calculate_risk_analysis(results)


# 3. Verify scoring raises InsufficientEvidenceError if no supporting source is present
@patch("app.risk.engine.load_config", return_value=POLICY_ENABLED_CONFIG)
def test_missing_supporting_source_raises_error(mock_load):
    results = [
        make_test_result("R1", "GST Portal"),
        make_test_result("R2", "MCA Portal"),
    ]
    with pytest.raises(InsufficientEvidenceError, match="supporting source"):
        calculate_risk_analysis(results)


# 4. Verify scoring succeeds if the policy is disabled in configuration
def test_policy_disabled_succeeds():
    mock_config = {
        "rules": {},
        "risk_levels": {},
        "minimum_evidence_policy": {
            "enabled": False
        }
    }
    results = [
        make_test_result("R1", "GST Portal")
    ]
    with patch("app.risk.engine.load_config", return_value=mock_config):
        analysis = calculate_risk_analysis(results)
        assert "overall_risk" in analysis


# 5. Verify endpoint /risk returns HTTP 422 with a clear detail message when policy is not met
@patch("app.risk.engine.load_config", return_value=POLICY_ENABLED_CONFIG)
def test_api_risk_insufficient_evidence_returns_422(mock_load, db_session, investigation_id):
    from datetime import datetime, timezone
    # Setup test DB evidence - only legal source, no supporting source
    ev = Evidence(
        investigation_id=investigation_id,
        research_result_id="R1",
        task_id="TASK-001",
        field_name="gst_status",
        field_value="Active",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_timestamp=datetime.now(timezone.utc),
        confidence=0.95
    )
    db_session.add(ev)
    db_session.commit()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    resp = client.get(f"/api/v1/investigations/{investigation_id}/risk")
    assert resp.status_code == 422
    data = resp.json()
    assert "Minimum evidence requirement not met" in data["detail"]

    app.dependency_overrides.clear()
