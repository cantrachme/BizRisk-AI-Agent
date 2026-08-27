import uuid
import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.graph.state import ResearchTask as GraphTask, ResearchResult
from app.models.investigation import Investigation
from app.models.evidence import Evidence
from app.models.research_task import ResearchTask as ResearchTaskModel
from app.services.research_task import (
    save_research_tasks,
    update_research_task_status,
    get_research_tasks_for_investigation,
)
from app.services.evidence import save_research_results, get_evidences_for_investigation


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
    inv = Investigation(input_data='{"business_name": "Persisted Company"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


def test_research_task_lifecycle_persistence(db_session, investigation_id):
    # 1. Create a task schema
    task1 = GraphTask(
        task_id="TASK-100",
        task_type="GST_VERIFICATION",
        target="GSTIN123",
        objective="Verify GST",
        required_fields=["legal_name"],
        priority=1,
    )

    # 2. Save it
    saved_tasks = save_research_tasks(db_session, [task1], investigation_id)
    assert len(saved_tasks) == 1
    assert saved_tasks[0].task_id == "TASK-100"
    assert saved_tasks[0].status == "PENDING"
    assert saved_tasks[0].retry_count == 0

    # 3. Transition to STARTED
    task_started = update_research_task_status(
        db_session, investigation_id, "TASK-100", "STARTED"
    )
    assert task_started is not None
    assert task_started.status == "STARTED"
    assert task_started.started_at is not None
    assert task_started.completed_at is None

    # 4. Transition to COMPLETED
    task_completed = update_research_task_status(
        db_session,
        investigation_id,
        "TASK-100",
        "COMPLETED",
        result='{"status": "success"}',
    )
    assert task_completed is not None
    assert task_completed.status == "COMPLETED"
    assert task_completed.completed_at is not None
    assert task_completed.result_info == '{"status": "success"}'

    # 5. Re-schedule/retry
    resaved_tasks = save_research_tasks(db_session, [task1], investigation_id)
    assert len(resaved_tasks) == 1
    assert resaved_tasks[0].status == "PENDING"
    assert resaved_tasks[0].retry_count == 1
    assert resaved_tasks[0].started_at is None
    assert resaved_tasks[0].completed_at is None


def test_evidence_linking_to_research_task(db_session, investigation_id):
    # 1. Create and persist research task
    task = GraphTask(
        task_id="TASK-200",
        task_type="MCA_VERIFICATION",
        target="CIN123",
        objective="Verify CIN",
        required_fields=["company_status"],
        priority=1,
    )
    save_research_tasks(db_session, [task], investigation_id)

    # 2. Create a research result referencing the task
    result = ResearchResult(
        result_id="RES-200",
        task_id="TASK-200",
        field_name="company_status",
        field_value="ACTIVE",
        source_name="MCA Portal",
        source_url="https://mca.gov.in",
        retrieved_at="2026-08-27T10:00:00+00:00",
        confidence=0.95,
    )

    # 3. Save result as evidence
    saved_ev = save_research_results(db_session, [result], investigation_id)
    assert len(saved_ev) == 1
    evidence = saved_ev[0]

    # Verify relationships and verification status
    assert evidence.research_result_id == "RES-200"
    assert evidence.verification_status == "UNVERIFIED"
    assert evidence.research_task_id is not None

    # Retrieve from DB and verify linked ResearchTask
    db_session.expire_all()
    evidence_db = db_session.get(Evidence, evidence.id)
    assert evidence_db.research_task is not None
    assert evidence_db.research_task.task_id == "TASK-200"
    assert len(evidence_db.research_task.evidences) == 1


def test_integration_research_flow_persistence(db_session, investigation_id):
    from app.graph.workflow import app as graph_app

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Patch session to use our in-memory SQLite db
    import unittest.mock as mock
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
        state = {
            "investigation_id": str(investigation_id),
            "raw_input": {
                "business_name": "Test Persisted LLC",
                "gstin": "27ABCDE1234F1Z5",
            },
            "normalized_input": {},
            "pending_tasks": [],
            "completed_tasks": [],
            "failed_tasks": [],
            "results": [],
            "planner_loop_count": 0,
            "qa_loop_count": 0,
            "status": "CREATED",
        }
        
        # We patch BrowserResearchAgent.execute to mock successful GST details return
        def mock_execute(self, task):
            if task.task_type == "GST_VERIFICATION":
                return [
                    ResearchResult(
                        result_id="RES-GST-001",
                        task_id=task.task_id,
                        field_name="gst_status",
                        field_value="Active",
                        source_name="GST Portal",
                        source_url="https://gst.gov.in",
                        retrieved_at=datetime.now(timezone.utc).isoformat(),
                        confidence=0.95,
                    )
                ]
            return []

        # Patch validate_report to return PASS so the graph terminates
        mock_qa_res = {
            "status": "PASS",
            "issues": [],
            "evidence_coverage": 1.0,
            "score_verified": True,
            "entity_verified": True
        }

        with mock.patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute), \
             mock.patch("app.services.qa.validate_report", return_value=mock_qa_res):
            graph_app.invoke(state)

    # Verify that planned tasks were saved to the DB
    tasks_db = get_research_tasks_for_investigation(db_session, investigation_id)
    assert len(tasks_db) > 0
    task_types = [t.task_type for t in tasks_db]
    assert "GST_VERIFICATION" in task_types

    # Verify status transitions were logged in the DB
    gst_task = [t for t in tasks_db if t.task_type == "GST_VERIFICATION"][0]
    assert gst_task.status == "COMPLETED"
    assert gst_task.started_at is not None
    assert gst_task.completed_at is not None

    # Verify that the corresponding evidence was persisted and is linked to the task
    evidences_db = get_evidences_for_investigation(db_session, investigation_id)
    assert len(evidences_db) > 0
    gst_evidence = [e for e in evidences_db if e.field_name == "gst_status"][0]
    assert gst_evidence.research_task_id == gst_task.id
