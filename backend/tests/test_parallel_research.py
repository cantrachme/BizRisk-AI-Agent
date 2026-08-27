import pytest
import uuid
import time
import json
import threading
from datetime import datetime, timezone
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.investigation import Investigation
from app.models.research_task import ResearchTask as DBResearchTask
from app.models.evidence import Evidence as DBEvidence
from app.models.source_registry import SourceRegistry
from app.services.source_registry import populate_default_sources
from app.core.exceptions import HumanInterventionRequiredException
from app.core.tracking import llm_calls_var, token_usage_var, browser_actions_var, browser_tasks_count_var
from app.graph.nodes import browser_node
from app.graph.state import InvestigationState, ResearchTask, ResearchResult


@pytest.fixture(name="session_factory")
def fixture_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )
    return TestingSessionLocal


@pytest.fixture(name="investigation_id")
def fixture_investigation_id(session_factory):
    db = session_factory()
    inv = Investigation(
        input_data='{"business_name": "Test Company"}',
        status="created"
    )
    db.add(inv)
    db.commit()
    inv_id = inv.id
    db.close()
    return inv_id


def test_concurrency_timing(session_factory, investigation_id):
    # Prepare 3 independent tasks
    tasks = [
        ResearchTask(
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="27ABCDE1234F1Z5",
            objective="Verify GST",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=["gst.gov.in"],
        ),
        ResearchTask(
            task_id="TASK-002",
            task_type="MCA_VERIFICATION",
            target="U12345MH2026PTC123456",
            objective="Verify CIN",
            required_fields=["company_status"],
            priority=1,
            preferred_sources=["mca.gov.in"],
        ),
        ResearchTask(
            task_id="TASK-003",
            task_type="WEBSITE_VERIFICATION",
            target="example.com",
            objective="Verify website",
            required_fields=["established_year"],
            priority=1,
            preferred_sources=["company_website"],
        ),
    ]

    db = session_factory()
    for t in tasks:
        db_task = DBResearchTask(
            investigation_id=investigation_id,
            task_id=t.task_id,
            task_type=t.task_type,
            target=t.target,
            objective=t.objective,
            status="PENDING"
        )
        db.add(db_task)
    db.commit()
    db.close()

    # Mock Browser Agent execute to sleep for 0.15s
    def mock_execute(self, task):
        print(f"[mock_execute] START: {task.task_id}", flush=True)
        time.sleep(0.15)
        print(f"[mock_execute] END: {task.task_id}", flush=True)
        return [
            ResearchResult(
                result_id=f"RES-{task.task_id}",
                task_id=task.task_id,
                field_name=task.required_fields[0],
                field_value="mocked_val",
                source_name="mock_source",
                source_url="http://mock.com",
                retrieved_at="2026-08-28T00:00:00Z",
                confidence=0.90
            )
        ]

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": tasks,
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "CREATED",
    }

    class MockSessionLocal:
        def __init__(self):
            print("[MockSessionLocal] init", flush=True)
            self.session = session_factory()
        def __enter__(self):
            print("[MockSessionLocal] enter", flush=True)
            return self.session
        def __exit__(self, exc_type, exc_val, exc_tb):
            print("[MockSessionLocal] exit - commit", flush=True)
            self.session.commit()
            print("[MockSessionLocal] exit - close", flush=True)
            self.session.close()
            print("[MockSessionLocal] exit - done", flush=True)

    start_time = time.time()
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = browser_node(state)
    elapsed = time.time() - start_time

    # Verification
    assert output_state["status"] == "RESEARCH_COMPLETED"
    assert len(output_state["completed_tasks"]) == 3
    assert elapsed < 0.35, f"Execution took too long: {elapsed:.2f}s"


def test_failure_isolation(session_factory, investigation_id):
    tasks = [
        ResearchTask(
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="27ABCDE1234F1Z5",
            objective="Verify GST",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=["gst.gov.in"],
        ),
        ResearchTask(
            task_id="TASK-002",
            task_type="MCA_VERIFICATION",
            target="U12345MH2026PTC123456",
            objective="Verify CIN",
            required_fields=["company_status"],
            priority=1,
            preferred_sources=["mca.gov.in"],
        ),
    ]

    db = session_factory()
    for t in tasks:
        db_task = DBResearchTask(
            investigation_id=investigation_id,
            task_id=t.task_id,
            task_type=t.task_type,
            target=t.target,
            objective=t.objective,
            status="PENDING"
        )
        db.add(db_task)
    db.commit()
    db.close()

    # Mock execute: TASK-001 succeeds, TASK-002 raises Exception (fails)
    def mock_execute(self, task):
        if task.task_id == "TASK-001":
            return [
                ResearchResult(
                    result_id="RES-001",
                    task_id=task.task_id,
                    field_name="legal_name",
                    field_value="Success Corp",
                    source_name="gst.gov.in",
                    source_url="http://gst.gov.in",
                    retrieved_at="2026-08-28T00:00:00Z",
                    confidence=0.95
                )
            ]
        else:
            raise Exception("Network Timeout on MCA")

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": tasks,
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "CREATED",
    }

    class MockSessionLocal:
        def __init__(self):
            self.session = session_factory()
        def __enter__(self):
            return self.session
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.session.commit()
            self.session.close()

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = browser_node(state)

    # Verification
    assert len(output_state["completed_tasks"]) == 1
    assert output_state["completed_tasks"][0].task_id == "TASK-001"
    assert len(output_state["failed_tasks"]) == 1
    assert output_state["failed_tasks"][0].task_id == "TASK-002"

    # Verify database updates
    db = session_factory()
    t1 = db.query(DBResearchTask).filter_by(task_id="TASK-001").one()
    t2 = db.query(DBResearchTask).filter_by(task_id="TASK-002").one()
    assert t1.status == "COMPLETED"
    assert t2.status == "FAILED"
    assert "Network Timeout on MCA" in t2.error_info

    # Verify successful evidence is persisted
    evs = db.query(DBEvidence).all()
    assert len(evs) == 1
    assert evs[0].field_value.strip('"') == 'Success Corp'
    db.close()


def test_hitl_compatibility(session_factory, investigation_id):
    tasks = [
        ResearchTask(
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="27ABCDE1234F1Z5",
            objective="Verify GST",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=["gst.gov.in"],
        ),
        ResearchTask(
            task_id="TASK-002",
            task_type="MCA_VERIFICATION",
            target="U12345MH2026PTC123456",
            objective="Verify CIN",
            required_fields=["company_status"],
            priority=1,
            preferred_sources=["mca.gov.in"],
        ),
    ]

    db = session_factory()
    for t in tasks:
        db_task = DBResearchTask(
            investigation_id=investigation_id,
            task_id=t.task_id,
            task_type=t.task_type,
            target=t.target,
            objective=t.objective,
            status="PENDING"
        )
        db.add(db_task)
    db.commit()
    db.close()

    # Mock execute: TASK-001 hits CAPTCHA (HITL), TASK-002 completes successfully
    def mock_execute(self, task):
        if task.task_id == "TASK-001":
            raise HumanInterventionRequiredException(
                message="Please solve CAPTCHA to continue",
                intervention_type="CAPTCHA"
            )
        else:
            return [
                ResearchResult(
                    result_id="RES-002",
                    task_id=task.task_id,
                    field_name="company_status",
                    field_value="Active",
                    source_name="mca.gov.in",
                    source_url="http://mca.gov.in",
                    retrieved_at="2026-08-28T00:00:00Z",
                    confidence=0.95
                )
            ]

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": tasks,
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "CREATED",
    }

    class MockSessionLocal:
        def __init__(self):
            self.session = session_factory()
        def __enter__(self):
            return self.session
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.session.commit()
            self.session.close()

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = browser_node(state)

    # Verification
    assert output_state["status"] == "WAITING_FOR_USER"
    assert len(output_state["completed_tasks"]) == 1
    assert output_state["completed_tasks"][0].task_id == "TASK-002"
    assert len(output_state["pending_tasks"]) == 1
    assert output_state["pending_tasks"][0].task_id == "TASK-001"
    assert output_state["pending_tasks"][0].status == "HUMAN_INTERVENTION_REQUIRED"

    # Verify database updates
    db = session_factory()
    t1 = db.query(DBResearchTask).filter_by(task_id="TASK-001").one()
    t2 = db.query(DBResearchTask).filter_by(task_id="TASK-002").one()
    assert t1.status == "HUMAN_INTERVENTION_REQUIRED"
    assert t1.intervention_type == "CAPTCHA"
    assert t2.status == "COMPLETED"
    db.close()


def test_caching_and_mixed_execution(session_factory, investigation_id):
    db = session_factory()
    populate_default_sources(db)
    
    db_task_prev = DBResearchTask(
        investigation_id=investigation_id,
        task_id="TASK-PREV",
        task_type="GST_VERIFICATION",
        target="27ABCDE1234F1Z5",
        objective="Verify GST",
        status="COMPLETED"
    )
    db.add(db_task_prev)
    db.commit()
    db.refresh(db_task_prev)
    
    cached_ev = DBEvidence(
        investigation_id=investigation_id,
        research_result_id="RES-CACHE-001",
        task_id="TASK-PREV",
        research_task_id=db_task_prev.id,
        field_name="legal_name",
        field_value='"Acme Cache Corp"',
        source_name="gst.gov.in",
        retrieved_timestamp=datetime.now(timezone.utc),
        confidence=0.99
    )
    db.add(cached_ev)
    db.commit()
    db.close()

    tasks = [
        ResearchTask(
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="27ABCDE1234F1Z5",
            objective="Verify GST",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=["gst.gov.in"],
        ),
        ResearchTask(
            task_id="TASK-002",
            task_type="MCA_VERIFICATION",
            target="U12345MH2026PTC123456",
            objective="Verify CIN",
            required_fields=["company_status"],
            priority=1,
            preferred_sources=["mca.gov.in"],
        ),
    ]

    db = session_factory()
    for t in tasks:
        db_task = DBResearchTask(
            investigation_id=investigation_id,
            task_id=t.task_id,
            task_type=t.task_type,
            target=t.target,
            objective=t.objective,
            status="PENDING"
        )
        db.add(db_task)
    db.commit()
    db.close()

    # Browser execute should only run for TASK-002
    def mock_execute(self, task):
        assert task.task_id == "TASK-002"
        return [
            ResearchResult(
                result_id="RES-002",
                task_id=task.task_id,
                field_name="company_status",
                field_value="Active",
                source_name="mca.gov.in",
                source_url="http://mca.gov.in",
                retrieved_at="2026-08-28T00:00:00Z",
                confidence=0.95
            )
        ]

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": tasks,
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "CREATED",
    }

    class MockSessionLocal:
        def __init__(self):
            self.session = session_factory()
        def __enter__(self):
            return self.session
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.session.commit()
            self.session.close()

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute):
        output_state = browser_node(state)

    # Verification
    assert len(output_state["completed_tasks"]) == 2
    assert len(output_state["results"]) == 2
    vals = [r.field_value for r in output_state["results"]]
    assert "Acme Cache Corp" in vals
    assert "Active" in vals


def test_resource_limits_enforcement(session_factory, investigation_id):
    tasks = [
        ResearchTask(
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="27ABCDE1234F1Z5",
            objective="Verify GST",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=["gst.gov.in"],
        ),
        ResearchTask(
            task_id="TASK-002",
            task_type="MCA_VERIFICATION",
            target="U12345MH2026PTC123456",
            objective="Verify CIN",
            required_fields=["company_status"],
            priority=1,
            preferred_sources=["mca.gov.in"],
        ),
    ]

    db = session_factory()
    for t in tasks:
        db_task = DBResearchTask(
            investigation_id=investigation_id,
            task_id=t.task_id,
            task_type=t.task_type,
            target=t.target,
            objective=t.objective,
            status="PENDING"
        )
        db.add(db_task)
    db.commit()
    db.close()

    from app.core.config import Settings

    def mock_execute(self, task):
        return [
            ResearchResult(
                result_id=f"RES-{task.task_id}",
                task_id=task.task_id,
                field_name=task.required_fields[0],
                field_value="Val",
                source_name="source",
                source_url="http://source.com",
                retrieved_at="2026-08-28T00:00:00Z",
                confidence=0.95
            )
        ]

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": tasks,
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "CREATED",
        "browser_tasks_count": 0,
        "browser_actions": 0,
    }

    class MockSessionLocal:
        def __init__(self):
            self.session = session_factory()
        def __enter__(self):
            return self.session
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.session.commit()
            self.session.close()

    mock_settings = Settings()
    mock_settings.max_research_tasks = 1
    mock_settings.max_browser_actions = 5

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", mock_execute), \
         mock.patch("app.core.config.get_settings", return_value=mock_settings):
        output_state = browser_node(state)

    # Verification: Only 1 task should be completed, 1 should be remaining pending with status LIMIT_REACHED
    assert output_state["status"] == "LIMIT_REACHED"
    assert len(output_state["completed_tasks"]) == 1
    assert len(output_state["pending_tasks"]) == 1
    assert output_state["pending_tasks"][0].task_id == "TASK-002"
