import pytest
import uuid
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime, timezone

from app.db.base import Base
from app.models.investigation import Investigation
from app.models.report import Report
from app.models.evidence import Evidence


@pytest.fixture(name="db")
def fixture_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def validate_report_grounding(db, report: Report) -> dict:
    report_data = json.loads(report.report_json)
    investigation_id = report.investigation_id
    
    # Load all valid evidence IDs for this investigation from the DB
    valid_evs = db.query(Evidence).filter(Evidence.investigation_id == investigation_id).all()
    valid_ev_ids = {e.research_result_id for e in valid_evs}
    
    errors = []
    
    # 1. Validate major findings evidence references
    for idx, finding in enumerate(report_data.get("major_findings", [])):
        for ev_id in finding.get("evidence_ids", []):
            if ev_id not in valid_ev_ids:
                errors.append(f"Finding {idx} ({finding.get('code')}) references invalid/cross-investigation evidence ID: {ev_id}")
                
    # 2. Validate evidence summary references
    for idx, summary in enumerate(report_data.get("evidence_summary", [])):
        ev_id = summary.get("evidence_id")
        if ev_id not in valid_ev_ids:
            errors.append(f"Evidence summary {idx} references invalid/cross-investigation evidence ID: {ev_id}")
            
    return {
        "valid": len(errors) == 0,
        "errors": errors
    }


def test_report_grounding_regression(db):
    # Create Investigation A
    inv_a = Investigation(
        id=uuid.uuid4(),
        status="CREATED",
        user_id="UserA",
        input_data="{}"
    )
    db.add(inv_a)
    
    # Create valid evidence for Investigation A
    ev_a1 = Evidence(
        id=uuid.uuid4(),
        investigation_id=inv_a.id,
        research_result_id="EV-A-001",
        task_id="TASK-001",
        field_name="gst_status",
        field_value="active",
        source_name="GST",
        confidence=0.95,
        retrieved_timestamp=datetime.now(timezone.utc)
    )
    db.add(ev_a1)
    db.commit()

    # Case 1: Valid Grounding report
    report_json_valid = {
        "major_findings": [
            {
                "code": "GST_INACTIVE",
                "evidence_ids": ["EV-A-001"]
            }
        ],
        "evidence_summary": [
            {
                "evidence_id": "EV-A-001"
            }
        ]
    }
    
    report_valid = Report(
        id=uuid.uuid4(),
        investigation_id=inv_a.id,
        report_json=json.dumps(report_json_valid),
        version=1
    )
    
    res_valid = validate_report_grounding(db, report_valid)
    assert res_valid["valid"] is True
    assert len(res_valid["errors"]) == 0


    # Case 2: Fake/Non-existent Evidence ID
    report_json_fake = {
        "major_findings": [
            {
                "code": "GST_INACTIVE",
                "evidence_ids": ["EV-FAKE-999"]
            }
        ],
        "evidence_summary": [
            {
                "evidence_id": "EV-A-001"
            }
        ]
    }
    
    report_fake = Report(
        id=uuid.uuid4(),
        investigation_id=inv_a.id,
        report_json=json.dumps(report_json_fake),
        version=2
    )
    
    res_fake = validate_report_grounding(db, report_fake)
    assert res_fake["valid"] is False
    assert any("invalid/cross-investigation" in err for err in res_fake["errors"])


    # Case 3: Cross-investigation Evidence ID
    # Create Investigation B with its own evidence
    inv_b = Investigation(
        id=uuid.uuid4(),
        status="CREATED",
        user_id="UserB",
        input_data="{}"
    )
    db.add(inv_b)
    
    ev_b1 = Evidence(
        id=uuid.uuid4(),
        investigation_id=inv_b.id,
        research_result_id="EV-B-001",
        task_id="TASK-002",
        field_name="company_status",
        field_value="active",
        source_name="MCA",
        confidence=0.95,
        retrieved_timestamp=datetime.now(timezone.utc)
    )
    db.add(ev_b1)
    db.commit()

    # Report for Investigation A referencing Investigation B's evidence ID
    report_json_cross = {
        "major_findings": [
            {
                "code": "GST_INACTIVE",
                "evidence_ids": ["EV-A-001"]
            }
        ],
        "evidence_summary": [
            {
                "evidence_id": "EV-B-001" # Belongs to B, not A!
            }
        ]
    }
    
    report_cross = Report(
        id=uuid.uuid4(),
        investigation_id=inv_a.id,
        report_json=json.dumps(report_json_cross),
        version=3
    )
    
    res_cross = validate_report_grounding(db, report_cross)
    assert res_cross["valid"] is False
    assert any("references invalid/cross-investigation" in err for err in res_cross["errors"])
