import uuid
import json
from datetime import datetime, timezone, timedelta
import pytest
import concurrent.futures
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.graph.state import ResearchResult
from app.models.investigation import Investigation
from app.models.evidence import Evidence
from app.models.report import Report
from app.services.evidence import save_research_result
from app.services.report import generate_investigation_report
from app.services.qa import validate_report, validate_report_grounding


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
    inv = Investigation(input_data='{"business_name": "Reproduce Corp", "gstin": "27ABCDE1234F1Z5"}')
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


# 1. Versioning tests (1, 2, 3)
def test_report_version_progression(db_session, investigation_id):
    # Save candidate entities so entity resolution is not empty (allows QA PASS)
    cand = make_test_result(
        result_id="C1",
        field_name="candidate_entities",
        field_value=[{"business_name": "Reproduce Corp", "gstin": "27ABCDE1234F1Z5", "confidence": 0.95, "match_type": "EXACT"}]
    )
    res = make_test_result(result_id="RES-V1", field_name="gst_status", field_value="Active")
    save_research_result(db_session, cand, investigation_id)
    save_research_result(db_session, res, investigation_id)

    # First report generation -> v1
    r1 = generate_investigation_report(db_session, investigation_id)
    assert r1["meta"]["report_version"] == "1"

    # Verify record in DB
    reps = db_session.query(Report).filter(Report.investigation_id == investigation_id).order_by(Report.version.asc()).all()
    assert len(reps) == 1
    assert reps[0].version == 1
    assert reps[0].qa_status == "PENDING"

    # Validate report -> changes status to PASS (since it has valid entity and matched risk score)
    validate_report(db_session, investigation_id)
    assert reps[0].qa_status == "PASS"

    # Second report generation -> v2
    r2 = generate_investigation_report(db_session, investigation_id)
    assert r2["meta"]["report_version"] == "2"

    reps = db_session.query(Report).filter(Report.investigation_id == investigation_id).order_by(Report.version.asc()).all()
    assert len(reps) == 2
    assert reps[0].version == 1
    assert reps[0].qa_status == "PASS"
    assert reps[1].version == 2
    assert reps[1].qa_status == "PENDING"

    # Third report generation -> v3
    r3 = generate_investigation_report(db_session, investigation_id)
    assert r3["meta"]["report_version"] == "3"

    reps = db_session.query(Report).filter(Report.investigation_id == investigation_id).order_by(Report.version.asc()).all()
    assert len(reps) == 3
    assert reps[0].version == 1
    assert reps[1].version == 2
    assert reps[2].version == 3


# 2. Evidence Grounding tests
def test_evidence_grounding_validation(db_session, investigation_id):
    # Save a valid evidence record
    res = make_test_result(result_id="RES-OK", field_name="gst_status", field_value="Active")
    save_research_result(db_session, res, investigation_id)

    # A: Valid Evidence ID -> passes grounding check
    report_valid = {
        "major_findings": [
            {
                "description": "GST is Active",
                "evidence_ids": ["RES-OK"],
                "code": "GST_ACTIVE"
            }
        ]
    }
    res_valid = validate_report_grounding(db_session, investigation_id, report_valid)
    assert res_valid["is_valid"] is True
    assert len(res_valid["issues"]) == 0

    # B: Missing Evidence IDs list -> fails grounding check
    report_missing_list = {
        "major_findings": [
            {
                "description": "GST is Active",
                "code": "GST_ACTIVE"
            }
        ]
    }
    res_missing = validate_report_grounding(db_session, investigation_id, report_missing_list)
    assert res_missing["is_valid"] is False
    assert any("no supporting evidence IDs list" in i["finding"] for i in res_missing["issues"])

    # C: Non-existent Evidence ID -> fails grounding check
    report_nonexistent = {
        "major_findings": [
            {
                "description": "GST is Inactive",
                "evidence_ids": ["RES-NOT-EXISTS"],
                "code": "GST_INACTIVE"
            }
        ]
    }
    res_nonexistent = validate_report_grounding(db_session, investigation_id, report_nonexistent)
    assert res_nonexistent["is_valid"] is False
    assert any("references non-existent evidence ID" in i["finding"] for i in res_nonexistent["issues"])

    # D: Evidence from another investigation -> fails grounding check
    other_inv_id = uuid.uuid4()
    # Save evidence to other investigation
    res_other = make_test_result(result_id="RES-OTHER", field_name="gst_status", field_value="Active")
    save_research_result(db_session, res_other, other_inv_id)

    report_other = {
        "major_findings": [
            {
                "description": "GST is Inactive",
                "evidence_ids": ["RES-OTHER"],
                "code": "GST_INACTIVE"
            }
        ]
    }
    res_other_val = validate_report_grounding(db_session, investigation_id, report_other)
    assert res_other_val["is_valid"] is False
    assert any("belonging to another investigation" in i["finding"] for i in res_other_val["issues"])

    # E: Orphan claim (required evidence missing but list is empty) -> fails grounding check
    report_orphan = {
        "major_findings": [
            {
                "description": "GST is Inactive",
                "evidence_ids": [],
                "code": "GST_INACTIVE"
            }
        ]
    }
    res_orphan = validate_report_grounding(db_session, investigation_id, report_orphan)
    assert res_orphan["is_valid"] is False
    assert any("required evidence references missing" in i["finding"] for i in res_orphan["issues"])


# 3. Metadata Preservation tests
def test_metadata_preservation(db_session, investigation_id):
    res = make_test_result(result_id="RES-METADATA", field_name="gst_status", field_value="Active")
    save_research_result(db_session, res, investigation_id)

    r = generate_investigation_report(db_session, investigation_id, prompt_version="v2")
    meta = r["meta"]

    # Check rule version
    assert meta["rule_version"] == "1.0.0"

    # Check prompt version
    assert meta["prompt_version"]["report"] == "v2"

    # Check model version
    assert meta["model_version"] is not None

    # Check timezone-aware timestamp
    generated_at = datetime.fromisoformat(meta["generated_at"])
    assert generated_at.tzinfo is not None

    # Verify in DB record
    db_rep = db_session.query(Report).filter(Report.investigation_id == investigation_id).first()
    assert db_rep.created_at is not None
    assert db_rep.version == 1
    assert db_rep.qa_status == "PENDING"


# 4. Concurrent Report Generation Safety
def test_concurrent_generation_safety(db_session, investigation_id):
    res = make_test_result(result_id="RES-CONC", field_name="gst_status", field_value="Active")
    save_research_result(db_session, res, investigation_id)

    # Use thread-local Session Local factory for thread safety
    SessionLocal = sessionmaker(bind=db_session.bind)

    # Launch 5 concurrent report generations for the same investigation
    def run_gen():
        with SessionLocal() as db:
            return generate_investigation_report(db, investigation_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_gen) for _ in range(5)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Assert all returned distinct versions: 1, 2, 3, 4, 5
    versions = {r["meta"]["report_version"] for r in results}
    assert len(versions) == 5
    assert versions == {"1", "2", "3", "4", "5"}

    # Assert exactly 5 report records are persisted in database
    db_reports = db_session.query(Report).filter(Report.investigation_id == investigation_id).all()
    assert len(db_reports) == 5
    db_versions = {rep.version for rep in db_reports}
    assert db_versions == {1, 2, 3, 4, 5}
