import pytest
import uuid
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.investigation import Investigation
from app.models.report import Report
from app.models.evidence import Evidence
from app.risk.engine import calculate_risk_analysis, InsufficientEvidenceError
from app.services.qa import validate_report, validate_report_grounding
from app.services.report import generate_investigation_report, generate_recommendation
from app.graph.workflow import should_continue_after_qa
from app.graph.state import ResearchResult, InvestigationState


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
    inv = Investigation(input_data='{"business_name": "Acme Corp", "gstin": "27ABCDE1234F1Z5"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


# 1. Deterministic Risk Scoring Tests
def test_deterministic_scoring_cap_and_levels():
    # Test capping at 100 and level calculation
    res1 = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="gst_status",
        field_value="Inactive",
        source_name="gst.gov.in",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.95
    )
    res2 = ResearchResult(
        result_id="RES-002",
        task_id="TASK-002",
        field_name="legal_name",
        field_value="Acme Corp",
        source_name="gst.gov.in",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.95
    )
    res3 = ResearchResult(
        result_id="RES-003",
        task_id="TASK-002",
        field_name="legal_name",
        field_value="Beta LLC",
        source_name="generic_web",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.50
    )

    analysis = calculate_risk_analysis([res1, res2, res3])
    score = analysis["overall_risk"]["score"]
    level = analysis["overall_risk"]["level"]

    assert 0 <= score <= 100
    assert level in {"LOW", "MODERATE", "HIGH", "CRITICAL", "UNKNOWN"}
    for signal in analysis["risk_signals"]:
        assert len(signal["evidence_ids"]) > 0


# 2. Evidence Grounding & Report Auditability
def test_report_auditability_metadata(db_session, investigation_id):
    from app.services.evidence import save_research_result

    cand = ResearchResult(
        result_id="RES-101",
        task_id="TASK-101",
        field_name="candidate_entities",
        field_value=[{"business_name": "Acme Corp", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95}],
        source_name="generic_web",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.95
    )
    res = ResearchResult(
        result_id="RES-102",
        task_id="TASK-102",
        field_name="gst_status",
        field_value="Active",
        source_name="gst.gov.in",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.95
    )
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    report = generate_investigation_report(db_session, investigation_id)

    assert "meta" in report
    meta = report["meta"]
    assert "rule_version" in meta
    assert "report_version" in meta
    assert "prompt_version" in meta
    assert "model_version" in meta
    assert "generated_at" in meta

    qa_res = validate_report(db_session, investigation_id)
    assert qa_res["status"] == "PASS"

    latest_report = db_session.query(Report).filter_by(investigation_id=investigation_id).first()
    assert latest_report is not None
    assert latest_report.qa_status == "PASS"


# 3. QA Failure Routing Matrix
def test_qa_routing_matrix_complete():
    # Wrong Entity -> entity_resolution
    state_entity = {
        "qa_result": {"status": "FAIL", "issues": [{"type": "WRONG_ENTITY", "finding": "Low confidence"}]},
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state_entity) == "entity_resolution"

    # Missing Evidence -> planner
    state_evidence = {
        "qa_result": {"status": "FAIL", "issues": [{"type": "MISSING_EVIDENCE", "finding": "Orphan claim"}]},
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state_evidence) == "planner"

    # Wrong Risk Score -> risk_analysis
    state_score = {
        "qa_result": {"status": "FAIL", "issues": [{"type": "WRONG_RISK_SCORE", "finding": "Score mismatch"}]},
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state_score) == "risk_analysis"

    # Report Wording -> report_generation
    state_wording = {
        "qa_result": {"status": "FAIL", "issues": [{"type": "REPORT_WORDING", "finding": "Forbidden word"}]},
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state_wording) == "report_generation"

    # Max retries reached -> __end__
    state_max_retry = {
        "qa_result": {"status": "FAIL", "issues": [{"type": "REPORT_WORDING", "finding": "Forbidden word"}]},
        "qa_loop_count": 2
    }
    assert should_continue_after_qa(state_max_retry) == "__end__"
