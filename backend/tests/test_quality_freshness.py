import uuid
import pytest
from datetime import datetime, timezone, timedelta

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
from app.services.evidence import save_research_results, get_evidences_for_investigation, is_evidence_fresh


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
    inv = Investigation(input_data='{"business_name": "Quality Corp"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


def test_research_task_deduplication(db_session, investigation_id):
    # Identical ResearchTask deduplication
    task1 = GraphTask(
        task_id="TASK-A",
        task_type="GST_VERIFICATION",
        target="GSTIN1",
        objective="Verify GSTIN1 status",
        required_fields=["gst_status"],
        priority=1,
    )
    # Re-scheduled/identical planner task (different graph ID but same content)
    task1_dup = GraphTask(
        task_id="TASK-B",
        task_type="GST_VERIFICATION",
        target="GSTIN1",
        objective="Verify GSTIN1 status",
        required_fields=["gst_status"],
        priority=1,
    )

    save_research_tasks(db_session, [task1], investigation_id)
    save_research_tasks(db_session, [task1_dup], investigation_id)

    tasks_db = get_research_tasks_for_investigation(db_session, investigation_id)
    # Check that repeated execution did not duplicate rows
    assert len(tasks_db) == 1
    assert tasks_db[0].task_id == "TASK-B"  # Updated task_id


def test_research_task_distinct_target_objective(db_session, investigation_id):
    # Different target creates separate task
    task1 = GraphTask(
        task_id="TASK-1",
        task_type="GST_VERIFICATION",
        target="GSTIN1",
        objective="Verify GSTIN1 status",
        required_fields=["gst_status"],
        priority=1,
    )
    task_diff_target = GraphTask(
        task_id="TASK-2",
        task_type="GST_VERIFICATION",
        target="GSTIN2",
        objective="Verify GSTIN1 status",
        required_fields=["gst_status"],
        priority=1,
    )
    # Different objective creates separate task
    task_diff_objective = GraphTask(
        task_id="TASK-3",
        task_type="GST_VERIFICATION",
        target="GSTIN1",
        objective="Verify GSTIN1 with extra checks",
        required_fields=["gst_status"],
        priority=1,
    )

    save_research_tasks(db_session, [task1, task_diff_target, task_diff_objective], investigation_id)
    tasks_db = get_research_tasks_for_investigation(db_session, investigation_id)
    assert len(tasks_db) == 3


def test_evidence_deduplication_and_protection(db_session, investigation_id):
    # Identical Evidence deduplication (same investigation, field, and source)
    res_base = ResearchResult(
        result_id="RES-01",
        task_id="TASK-X",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at="2026-08-27T10:00:00+00:00",
        confidence=0.90,
    )

    # Newer evidence replaces older evidence
    res_newer = ResearchResult(
        result_id="RES-01",
        task_id="TASK-X",
        field_name="gst_status",
        field_value="ACTIVE-NEW",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at="2026-08-27T12:00:00+00:00",
        confidence=0.95,
    )

    # Older evidence cannot replace newer evidence (stale protection)
    res_stale = ResearchResult(
        result_id="RES-01",
        task_id="TASK-X",
        field_name="gst_status",
        field_value="ACTIVE-STALE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at="2026-08-27T08:00:00+00:00",
        confidence=0.80,
    )

    # Equal timestamps behave deterministically
    res_equal = ResearchResult(
        result_id="RES-01",
        task_id="TASK-X",
        field_name="gst_status",
        field_value="ACTIVE-EQUAL",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at="2026-08-27T12:00:00+00:00",
        confidence=0.95,
    )

    # Save base
    save_research_results(db_session, [res_base], investigation_id)
    ev_list = get_evidences_for_investigation(db_session, investigation_id)
    assert len(ev_list) == 1
    assert ev_list[0].field_value == "ACTIVE"

    # Save newer -> check update
    save_research_results(db_session, [res_newer], investigation_id)
    ev_list = get_evidences_for_investigation(db_session, investigation_id)
    assert len(ev_list) == 1
    assert ev_list[0].field_value == "ACTIVE-NEW"

    # Save stale -> check ignored
    save_research_results(db_session, [res_stale], investigation_id)
    ev_list = get_evidences_for_investigation(db_session, investigation_id)
    assert len(ev_list) == 1
    assert ev_list[0].field_value == "ACTIVE-NEW"

    # Save equal timestamp -> check deterministic overwrite/update in place
    save_research_results(db_session, [res_equal], investigation_id)
    ev_list = get_evidences_for_investigation(db_session, investigation_id)
    assert len(ev_list) == 1
    assert ev_list[0].field_value == "ACTIVE-EQUAL"


def test_evidence_distinct_source_preservation(db_session, investigation_id):
    # Different source remains separate
    res1 = ResearchResult(
        result_id="RES-1",
        task_id="TASK-X",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at="2026-08-27T10:00:00+00:00",
        confidence=0.90,
    )
    res2 = ResearchResult(
        result_id="RES-2",
        task_id="TASK-Y",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="Third-Party Lookup",
        source_url="https://thirdparty.com",
        retrieved_at="2026-08-27T10:00:00+00:00",
        confidence=0.70,
    )

    save_research_results(db_session, [res1, res2], investigation_id)
    ev_list = get_evidences_for_investigation(db_session, investigation_id)
    assert len(ev_list) == 2


def test_freshness_limits(db_session):
    now = datetime.now(timezone.utc)

    # GST freshness (limit: 7 days)
    gst_fresh = now - timedelta(days=5)
    gst_stale = now - timedelta(days=8)
    assert is_evidence_fresh(gst_fresh, "gst_status") is True
    assert is_evidence_fresh(gst_stale, "gst_status") is False

    # MCA freshness (limit: 30 days)
    mca_fresh = now - timedelta(days=25)
    mca_stale = now - timedelta(days=32)
    assert is_evidence_fresh(mca_fresh, "mca_status") is True
    assert is_evidence_fresh(mca_stale, "mca_status") is False

    # Website freshness (limit: 30 days)
    web_fresh = now - timedelta(days=25)
    web_stale = now - timedelta(days=32)
    assert is_evidence_fresh(web_fresh, "website_status") is True
    assert is_evidence_fresh(web_stale, "website_status") is False

    # Default freshness (limit: 30 days)
    default_fresh = now - timedelta(days=25)
    default_stale = now - timedelta(days=32)
    assert is_evidence_fresh(default_fresh, "other_field") is True
    assert is_evidence_fresh(default_stale, "other_field") is False


def test_research_reuse_behavior(db_session, investigation_id):
    from app.graph.nodes import browser_node

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    import unittest.mock as mock
    
    # 1. Fresh evidence scenario -> Reuses result, skips browser
    task = GraphTask(
        task_id="TASK-F",
        task_type="GST_VERIFICATION",
        target="GSTIN-F",
        objective="Verify GSTIN-F status",
        required_fields=["gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    save_research_tasks(db_session, [task], investigation_id)

    # Insert fresh evidence directly
    res = ResearchResult(
        result_id="RES-FRESH",
        task_id="TASK-F",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.99,
    )
    save_research_results(db_session, [res], investigation_id)

    state = {
        "investigation_id": str(investigation_id),
        "pending_tasks": [task],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
    }

    # If it reuses, it will NOT call agent.execute
    agent_mock = mock.Mock()
    
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", agent_mock.execute):
        out = browser_node(state)

    agent_mock.execute.assert_not_called()
    assert len(out["completed_tasks"]) == 1
    assert out["completed_tasks"][0].status == "COMPLETED"
    assert len(out["results"]) == 1
    assert out["results"][0].field_value == "ACTIVE"


def test_research_reuse_expired_or_incomplete_behavior(db_session, investigation_id):
    from app.graph.nodes import browser_node

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    import unittest.mock as mock

    # 2. Expired evidence scenario -> execute normally
    task_expired = GraphTask(
        task_id="TASK-EXP",
        task_type="GST_VERIFICATION",
        target="GSTIN-EXP",
        objective="Verify GSTIN-EXP status",
        required_fields=["gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    save_research_tasks(db_session, [task_expired], investigation_id)

    # Insert expired/stale evidence directly (8 days ago)
    stale_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    res_stale = ResearchResult(
        result_id="RES-STALE",
        task_id="TASK-EXP",
        field_name="gst_status",
        field_value="OLD-VAL",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at=stale_time,
        confidence=0.99,
    )
    save_research_results(db_session, [res_stale], investigation_id)

    state = {
        "investigation_id": str(investigation_id),
        "pending_tasks": [task_expired],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
    }

    mock_new_res = [
        ResearchResult(
            result_id="RES-NEW-GST",
            task_id="TASK-EXP",
            field_name="gst_status",
            field_value="ACTIVE-NEW",
            source_name="GST Portal",
            source_url="https://gst.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.99,
        )
    ]
    agent_mock = mock.Mock()
    agent_mock.execute.return_value = mock_new_res

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", agent_mock.execute):
        out = browser_node(state)

    agent_mock.execute.assert_called_once_with(task_expired)
    assert len(out["completed_tasks"]) == 1
    assert out["results"][0].field_value == "ACTIVE-NEW"


def test_research_reuse_incomplete_behavior(db_session, investigation_id):
    from app.graph.nodes import browser_node

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    import unittest.mock as mock

    # 3. Incomplete evidence scenario -> required fields = ["gst_status", "legal_name"], but only gst_status is present
    task_inc = GraphTask(
        task_id="TASK-INC",
        task_type="GST_VERIFICATION",
        target="GSTIN-INC",
        objective="Verify GSTIN-INC status",
        required_fields=["gst_status", "legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    save_research_tasks(db_session, [task_inc], investigation_id)

    # Insert fresh gst_status evidence, but no legal_name
    res_gst = ResearchResult(
        result_id="RES-GST",
        task_id="TASK-INC",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.99,
    )
    save_research_results(db_session, [res_gst], investigation_id)

    state = {
        "investigation_id": str(investigation_id),
        "pending_tasks": [task_inc],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
    }

    mock_new_res = [
        ResearchResult(
            result_id="RES-GST",
            task_id="TASK-INC",
            field_name="gst_status",
            field_value="ACTIVE",
            source_name="GST Portal",
            source_url="https://gst.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.99,
        ),
        ResearchResult(
            result_id="RES-LEGAL",
            task_id="TASK-INC",
            field_name="legal_name",
            field_value="Quality Corp LLC",
            source_name="GST Portal",
            source_url="https://gst.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.99,
        ),
    ]
    agent_mock = mock.Mock()
    agent_mock.execute.return_value = mock_new_res

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", agent_mock.execute):
        out = browser_node(state)

    agent_mock.execute.assert_called_once_with(task_inc)
    assert len(out["completed_tasks"]) == 1
