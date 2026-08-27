import uuid
import pytest
from datetime import datetime, timezone, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.core.caching import ResolvedEntityCache, NormalizedNameCache
from app.entity_resolution.normalization import normalize_text, normalize_identifier
from app.graph.state import ResearchTask as GraphTask, ResearchResult
from app.models.investigation import Investigation
from app.models.evidence import Evidence
from app.models.research_task import ResearchTask as ResearchTaskModel
from app.services.research_task import save_research_tasks
from app.services.evidence import save_research_results, get_evidences_for_investigation, get_cached_source_result


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
    inv = Investigation(input_data='{"business_name": "Caching Corp"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


@pytest.fixture(autouse=True)
def clean_caches():
    ResolvedEntityCache.clear()
    NormalizedNameCache.clear()


# 1. Resolved Entity Cache Tests
def test_resolved_entity_cache_hit_miss(investigation_id):
    target = {"business_name": "Test Company", "gstin": "123", "cin": "456"}
    resolution_data = {"matched": True, "confidence": 0.99, "entity": {"business_name": "Test Company matched"}}

    # Cache miss initially
    assert ResolvedEntityCache.get(investigation_id, target) is None

    # Set cache
    ResolvedEntityCache.set(investigation_id, target, resolution_data)

    # Cache hit
    hit = ResolvedEntityCache.get(investigation_id, target)
    assert hit is not None
    assert hit["matched"] is True
    assert hit["confidence"] == 0.99
    assert hit["entity"]["business_name"] == "Test Company matched"


def test_resolved_entity_cache_safe_isolation():
    inv_a = uuid.uuid4()
    inv_b = uuid.uuid4()
    target = {"business_name": "Shared Name", "gstin": "123"}
    res_data = {"matched": True, "confidence": 1.0, "entity": {"name": "Match A"}}

    ResolvedEntityCache.set(inv_a, target, res_data)

    # Inv A should hit, Inv B should miss (no cross-user leaks)
    assert ResolvedEntityCache.get(inv_a, target) is not None
    assert ResolvedEntityCache.get(inv_b, target) is None


# 2. Normalized Name Cache Tests
def test_normalized_name_cache_hit_miss():
    input_val = "   abc  corp   "
    
    # Run first normalization -> populates cache
    res1 = normalize_text(input_val)
    assert res1 == "ABC CORP"

    # Make cache hit and assert same output
    res2 = normalize_text(input_val)
    assert res2 == "ABC CORP"


def test_normalized_name_cache_type_separation():
    input_val = "123 456"
    # Normalizing as text should keep spaces but uppercase
    norm_text = normalize_text(input_val)
    assert norm_text == "123 456"

    # Normalizing as identifier should strip spaces
    norm_id = normalize_identifier(input_val)
    assert norm_id == "123456"

    # Verify type-separated keys: cache has text version distinct from identifier version
    assert NormalizedNameCache.get("text", input_val) == "123 456"
    assert NormalizedNameCache.get("identifier", input_val) == "123456"


# 3. Source Result Caching Tests
def test_source_result_cache_hit_miss(db_session, investigation_id):
    # Setup matching task row
    task = GraphTask(
        task_id="TASK-X",
        task_type="GST_VERIFICATION",
        target="GSTIN-X",
        objective="Verify GSTIN",
        required_fields=["gst_status"],
        priority=1,
    )
    save_research_tasks(db_session, [task], investigation_id)

    # Missing initial cached result
    cached_miss = get_cached_source_result(
        db_session,
        task_type="GST_VERIFICATION",
        target="GSTIN-X",
        objective="Verify GSTIN",
        field_name="gst_status",
        source_name="GST Portal",
    )
    assert cached_miss is None

    # Insert fresh result
    res = ResearchResult(
        result_id="RES-01",
        task_id="TASK-X",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.99,
    )
    save_research_results(db_session, [res], investigation_id)

    # Valid fresh cache hit
    cached_hit = get_cached_source_result(
        db_session,
        task_type="GST_VERIFICATION",
        target="GSTIN-X",
        objective="Verify GSTIN",
        field_name="gst_status",
        source_name="GST Portal",
    )
    assert cached_hit is not None
    assert cached_hit.field_value == "ACTIVE"


def test_source_cache_different_source_does_not_collide(db_session, investigation_id):
    task = GraphTask(
        task_id="TASK-Y",
        task_type="GST_VERIFICATION",
        target="GSTIN-Y",
        objective="Verify GSTIN",
        required_fields=["gst_status"],
        priority=1,
    )
    save_research_tasks(db_session, [task], investigation_id)

    # Save evidence for GST Portal source
    res = ResearchResult(
        result_id="RES-GST",
        task_id="TASK-Y",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.99,
    )
    save_research_results(db_session, [res], investigation_id)

    # Check cache hit using different source name -> should miss (different sources must not collide)
    coll_miss = get_cached_source_result(
        db_session,
        task_type="GST_VERIFICATION",
        target="GSTIN-Y",
        objective="Verify GSTIN",
        field_name="gst_status",
        source_name="Third-Party Lookup",
    )
    assert coll_miss is None


def test_source_cache_different_objective_does_not_collide(db_session, investigation_id):
    task1 = GraphTask(
        task_id="TASK-1",
        task_type="GST_VERIFICATION",
        target="GSTIN-Z",
        objective="Verify GSTIN",
        required_fields=["gst_status"],
        priority=1,
    )
    save_research_tasks(db_session, [task1], investigation_id)

    # Save evidence
    res = ResearchResult(
        result_id="RES-Z",
        task_id="TASK-1",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=0.99,
    )
    save_research_results(db_session, [res], investigation_id)

    # Query using different objective -> should miss
    obj_miss = get_cached_source_result(
        db_session,
        task_type="GST_VERIFICATION",
        target="GSTIN-Z",
        objective="Deep check GSTIN",
        field_name="gst_status",
        source_name="GST Portal",
    )
    assert obj_miss is None


def test_source_cache_fresh_vs_expired(db_session, investigation_id):
    task = GraphTask(
        task_id="TASK-EXP",
        task_type="GST_VERIFICATION",
        target="GSTIN-EXP",
        objective="Verify GSTIN",
        required_fields=["gst_status"],
        priority=1,
    )
    save_research_tasks(db_session, [task], investigation_id)

    # Insert expired evidence (8 days old for GST status, threshold is 7 days)
    stale_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    res = ResearchResult(
        result_id="RES-OLD",
        task_id="TASK-EXP",
        field_name="gst_status",
        field_value="ACTIVE",
        source_name="GST Portal",
        source_url="https://gst.gov.in",
        retrieved_at=stale_time,
        confidence=0.99,
    )
    save_research_results(db_session, [res], investigation_id)

    # Expired cached result is not reused (miss)
    cached_val = get_cached_source_result(
        db_session,
        task_type="GST_VERIFICATION",
        target="GSTIN-EXP",
        objective="Verify GSTIN",
        field_name="gst_status",
        source_name="GST Portal",
    )
    assert cached_val is None


# 4. Workflow Nodes Browser Reuse Tests
def test_browser_reuse_all_fields_fresh(db_session, investigation_id):
    from app.graph.nodes import browser_node
    import unittest.mock as mock

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    task = GraphTask(
        task_id="TASK-REUSE",
        task_type="GST_VERIFICATION",
        target="GSTIN-REUSE",
        objective="Verify GSTIN",
        required_fields=["gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    save_research_tasks(db_session, [task], investigation_id)

    # Save fresh evidence
    res = ResearchResult(
        result_id="RES-GST",
        task_id="TASK-REUSE",
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

    agent_mock = mock.Mock()
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", agent_mock.execute):
        out = browser_node(state)

    # Browser execution must be skipped because fresh evidence exists
    agent_mock.execute.assert_not_called()
    assert len(out["completed_tasks"]) == 1
    assert out["completed_tasks"][0].status == "COMPLETED"


def test_browser_reuse_incomplete_fields_triggers_execution(db_session, investigation_id):
    from app.graph.nodes import browser_node
    import unittest.mock as mock

    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Required fields = ["gst_status", "legal_name"]
    task = GraphTask(
        task_id="TASK-INC",
        task_type="GST_VERIFICATION",
        target="GSTIN-INC",
        objective="Verify GSTIN",
        required_fields=["gst_status", "legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
    )
    save_research_tasks(db_session, [task], investigation_id)

    # Save fresh gst_status, but missing legal_name evidence
    res = ResearchResult(
        result_id="RES-GST",
        task_id="TASK-INC",
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

    agent_mock = mock.Mock()
    agent_mock.execute.return_value = [
        ResearchResult(
            result_id="RES-NEW",
            task_id="TASK-INC",
            field_name="legal_name",
            field_value="New Company Name",
            source_name="GST Portal",
            source_url="https://gst.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.99,
        )
    ]

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal), \
         mock.patch("app.agents.browser.BrowserResearchAgent.execute", agent_mock.execute):
        out = browser_node(state)

    # If even one required field is missing, execute the task normally (no bypass)
    agent_mock.execute.assert_called_once_with(task)
