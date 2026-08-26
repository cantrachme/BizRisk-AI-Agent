import os
import sys
import uuid
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Add backend directory to sys.path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
)

from app.db.base import Base
from app.db.session import get_db
from app.graph.state import ResearchResult
from app.main import app
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.models.risk_signal import RiskSignal
from app.services.evidence import save_research_result
from app.risk.engine import (
    calculate_risk_analysis,
    persist_risk_analysis,
)
from app.risk.rules import (
    normalize_evidence,
    evaluate_gst_inactive,
    evaluate_legal_name_conflict,
    evaluate_address_major_mismatch,
    evaluate_business_activity_mismatch,
    evaluate_very_recent_registration,
)


@pytest.fixture(name="db_session")
def fixture_db_session():
    # Setup thread-safe in-memory SQLite
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


def make_test_result(result_id="RES-001", **overrides):
    data = {
        "result_id": result_id,
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


# 1. Test Individual Rules
def test_gst_inactive_rule():
    res_active = normalize_evidence(make_test_result(field_value="Active"))
    assert evaluate_gst_inactive([res_active]) is None

    res_inactive = normalize_evidence(make_test_result(field_value="Inactive"))
    triggered = evaluate_gst_inactive([res_inactive])
    assert triggered is not None
    assert triggered["triggered"] is True
    assert triggered["evidence_ids"] == ["RES-001"]


def test_legal_name_conflict_rule():
    res1 = normalize_evidence(make_test_result(field_name="legal_name", field_value="Company A"))
    res2 = normalize_evidence(make_test_result(result_id="RES-002", field_name="legal_name", field_value="Company B"))

    # Conflict triggers
    triggered = evaluate_legal_name_conflict([res1, res2])
    assert triggered is not None
    assert triggered["triggered"] is True
    assert "RES-001" in triggered["evidence_ids"]
    assert "RES-002" in triggered["evidence_ids"]

    # Identical normalized names do not trigger conflict
    res1_alias = normalize_evidence(make_test_result(field_name="legal_name", field_value="Company A Pvt. Ltd."))
    res2_alias = normalize_evidence(make_test_result(result_id="RES-002", field_name="legal_name", field_value="COMPANY A PRIVATE LIMITED"))
    assert evaluate_legal_name_conflict([res1_alias, res2_alias]) is None


def test_address_major_mismatch_rule():
    res1 = normalize_evidence(make_test_result(field_name="address", field_value="Sector 62, Noida"))
    res2 = normalize_evidence(make_test_result(result_id="RES-002", field_name="address", field_value="Connaught Place, Delhi"))

    triggered = evaluate_address_major_mismatch([res1, res2])
    assert triggered is not None
    assert triggered["triggered"] is True

    res2_same = normalize_evidence(make_test_result(result_id="RES-002", field_name="address", field_value="Sector-62 Noida"))
    assert evaluate_address_major_mismatch([res1, res2_same]) is None


def test_business_activity_mismatch_rule():
    res1 = normalize_evidence(make_test_result(field_name="business_activity", field_value="IT Services"))
    res2 = normalize_evidence(make_test_result(result_id="RES-002", field_name="business_activity", field_value="Food Manufacturing"))

    triggered = evaluate_business_activity_mismatch([res1, res2])
    assert triggered is not None

    res2_same = normalize_evidence(make_test_result(result_id="RES-002", field_name="business_activity", field_value="it-services"))
    assert evaluate_business_activity_mismatch([res1, res2_same]) is None


def test_very_recent_registration_rule():
    # Registered on 2026-08-10, retrieved on 2026-08-26 (age < 1 year)
    res_recent = normalize_evidence(make_test_result(
        field_name="registration_date",
        field_value="2026-08-10",
        retrieved_at="2026-08-26T10:00:00+00:00"
    ))
    triggered = evaluate_very_recent_registration([res_recent])
    assert triggered is not None
    assert triggered["triggered"] is True

    # Registered on 2010-01-01 (age > 1 year)
    res_old = normalize_evidence(make_test_result(
        field_name="registration_date",
        field_value="2010-01-01",
        retrieved_at="2026-08-26T10:00:00+00:00"
    ))
    assert evaluate_very_recent_registration([res_old]) is None


# 2. Test Scoring Engine Mapping, Weights, Level, and Traceability
def test_calculate_risk_analysis():
    # Generate multiple mismatched results to verify sum of weights and level matching
    results = [
        make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive"), # GST_INACTIVE (weight 30)
        make_test_result(result_id="R2", field_name="legal_name", field_value="Company X"),
        make_test_result(result_id="R3", field_name="legal_name", field_value="Company Y"), # LEGAL_NAME_CONFLICT (weight 25)
        make_test_result(result_id="SUP-001", source_name="Company Website", field_name="website_status", field_value="Active"),
    ]
    analysis = calculate_risk_analysis(results)

    # 30 + 25 = 55 (Moderate Level: 31 to 60)
    assert analysis["overall_risk"]["score"] == 55
    assert analysis["overall_risk"]["level"] == "MODERATE"

    # Category score check
    assert analysis["category_scores"]["compliance"] == 30
    assert analysis["category_scores"]["identity"] == 25

    # Evidence traceability checks
    signals = {sig["code"]: sig for sig in analysis["risk_signals"]}
    assert "GST_INACTIVE" in signals
    assert signals["GST_INACTIVE"]["evidence_ids"] == ["R1"]
    assert "LEGAL_NAME_CONFLICT" in signals
    assert set(signals["LEGAL_NAME_CONFLICT"]["evidence_ids"]) == {"R2", "R3"}


def test_calculate_risk_analysis_score_cap():
    # Create enough active weights to exceed 100
    results = [
        make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive"), # 30
        make_test_result(result_id="R2", field_name="legal_name", field_value="Name X"),
        make_test_result(result_id="R3", field_name="legal_name", field_value="Name Y"), # 25
        make_test_result(result_id="R4", field_name="address", field_value="Addr A"),
        make_test_result(result_id="R5", field_name="address", field_value="Addr B"), # 10
        make_test_result(result_id="R6", field_name="business_activity", field_value="Act A"),
        make_test_result(result_id="R7", field_name="business_activity", field_value="Act B"), # 10
        make_test_result(result_id="R8", field_name="registration_date", field_value="2026-08-20", retrieved_at="2026-08-26T10:00:00+00:00"), # 5
        make_test_result(result_id="SUP-001", source_name="Company Website", field_name="website_status", field_value="Active"),
    ]
    analysis = calculate_risk_analysis(results)
    assert analysis["overall_risk"]["score"] == 80
    assert analysis["overall_risk"]["level"] == "HIGH"


# 3. Test Persistence
def test_persist_risk_analysis(db_session, investigation_id):
    results = [
        make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive"),
        make_test_result(result_id="SUP-001", source_name="Company Website", field_name="website_status", field_value="Active"),
    ]
    analysis = calculate_risk_analysis(results)

    persisted = persist_risk_analysis(db_session, investigation_id, analysis)
    assert len(persisted) == 1
    assert persisted[0].code == "GST_INACTIVE"

    # Query DB directly to check
    db_signals = db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).all()
    assert len(db_signals) == 1
    assert db_signals[0].code == "GST_INACTIVE"
    assert db_signals[0].risk_weight == 30
    assert db_signals[0].category == "COMPLIANCE"
    assert db_signals[0].severity == "HIGH"
    assert json.loads(db_signals[0].evidence_ids) == ["R1"]

    # Verify overwrite logic: running again deletes old signals
    new_results = [
        make_test_result(result_id="R2", field_name="legal_name", field_value="X"),
        make_test_result(result_id="R3", field_name="legal_name", field_value="Y"),
        make_test_result(result_id="SUP-001", source_name="Company Website", field_name="website_status", field_value="Active"),
    ]
    new_analysis = calculate_risk_analysis(new_results)
    persist_risk_analysis(db_session, investigation_id, new_analysis)

    db_signals2 = db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).all()
    assert len(db_signals2) == 1
    assert db_signals2[0].code == "LEGAL_NAME_CONFLICT"


# 4. Test Graph Integration
def test_graph_integration_populates_risk(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    # Patch SessionLocal to use our mock SQLite session
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Save a mismatching result first to trigger a signal
    res = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    save_research_result(db_session, res, investigation_id)

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
        "results": [res],
        "planner_loop_count": 0,
        "status": "CREATED",
    }

    def mock_execute(self, task):
        if task.task_type == "GST_VERIFICATION":
            return [
                ResearchResult(
                    result_id="R1",
                    task_id=task.task_id,
                    field_name="gst_status",
                    field_value="Inactive",
                    source_name="GST Portal",
                    retrieved_at="2026-08-26T10:00:00+00:00",
                    confidence=0.95,
                )
            ]
        return []

    with patch("app.db.session.SessionLocal", MockSessionLocal), patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = graph_app.invoke(initial_state)

    print("GRAPH OUTPUT STATE IS:", output_state)

    # Risk fields should be populated in the final state
    assert "overall_risk" in output_state
    assert output_state["overall_risk"]["score"] == 30
    assert output_state["overall_risk"]["level"] == "LOW"
    assert "category_scores" in output_state
    assert output_state["category_scores"]["compliance"] == 30

    # Verification of DB write in graph
    db_signals = db_session.query(RiskSignal).filter(RiskSignal.investigation_id == investigation_id).all()
    assert len(db_signals) == 1
    assert db_signals[0].code == "GST_INACTIVE"


# 5. Test API Routes
def test_get_investigation_risk_api(db_session, investigation_id):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    # Add some evidence to DB
    res1 = make_test_result(result_id="R1", field_name="gst_status", field_value="Inactive")
    res2 = make_test_result(result_id="R2", field_name="legal_name", field_value="A")
    res3 = make_test_result(result_id="R3", field_name="legal_name", field_value="B")
    save_research_result(db_session, res1, investigation_id)
    save_research_result(db_session, res2, investigation_id)
    save_research_result(db_session, res3, investigation_id)

    client = TestClient(app)
    response = client.get(f"/api/v1/investigations/{investigation_id}/risk")

    assert response.status_code == 200
    data = response.json()
    assert data["overall_risk"]["score"] == 55 # 30 + 25
    assert data["overall_risk"]["level"] == "MODERATE"
    assert len(data["risk_signals"]) == 2

    # Verify nonexistent investigation returns 404
    non_existent = uuid.uuid4()
    response_404 = client.get(f"/api/v1/investigations/{non_existent}/risk")
    assert response_404.status_code == 404

    app.dependency_overrides.clear()
