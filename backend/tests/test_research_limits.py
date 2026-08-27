import pytest
import uuid
import asyncio
from unittest import mock
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.core.config import get_settings
from app.graph.state import InvestigationState, ResearchTask as GraphTask, ResearchResult
from app.graph.nodes import discovery_node, planner_node, browser_node
from app.core.tracking import (
    llm_calls_var,
    token_usage_var,
    browser_actions_var,
    browser_tasks_count_var,
    check_limits,
)
from app.core.llm import get_llm_provider, LLMProviderException
from app.models.investigation import Investigation


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
    inv = Investigation(input_data='{"business_name": "Limits Corp"}')
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


@pytest.fixture(autouse=True)
def reset_tracking_vars():
    llm_calls_var.set(0)
    token_usage_var.set(0)
    browser_actions_var.set(0)
    browser_tasks_count_var.set(0)


def test_settings_validation():
    from app.core.config import Settings
    # Verify non-negative validation
    with pytest.raises(ValueError, match="Limit must be non-negative"):
        Settings(max_research_depth=-1)

    with pytest.raises(ValueError, match="Limit must be non-negative"):
        Settings(token_budget=-10)

    # Sensible defaults
    settings = get_settings()
    assert settings.max_research_depth == 3
    assert settings.max_browser_actions == 20
    assert settings.max_research_tasks == 15
    assert settings.max_llm_calls == 50
    assert settings.token_budget == 100000


def test_check_limits_depth():
    state: InvestigationState = {
        "planner_loop_count": 3,
        "browser_actions": 0,
        "browser_tasks_count": 0,
        "stop_reason": None,
    }

    settings = get_settings()
    
    # Under depth limit 4
    old_depth = settings.max_research_depth
    try:
        settings.max_research_depth = 4
        assert check_limits(state) is None

        # Reaches depth limit 3
        settings.max_research_depth = 3
        assert check_limits(state) == "Max research depth reached"
    finally:
        settings.max_research_depth = old_depth


def test_check_limits_browser_tasks():
    state: InvestigationState = {
        "planner_loop_count": 1,
        "stop_reason": None,
    }
    browser_tasks_count_var.set(5)

    settings = get_settings()
    old_tasks = settings.max_research_tasks
    try:
        settings.max_research_tasks = 5
        # Exact boundary check: 5 <= 5 is fine, but extra 1 task exceeds limit
        assert check_limits(state) is None
        assert check_limits(state, extra_tasks=1) == "Max browser/research tasks limit reached"
    finally:
        settings.max_research_tasks = old_tasks


def test_check_limits_browser_actions():
    state: InvestigationState = {
        "planner_loop_count": 1,
        "stop_reason": None,
    }
    browser_actions_var.set(10)

    settings = get_settings()
    old_actions = settings.max_browser_actions
    try:
        settings.max_browser_actions = 10
        # Exact boundary check: 10 <= 10 is fine, extra 1 action exceeds limit
        assert check_limits(state) is None
        assert check_limits(state, extra_actions=1) == "Max browser actions limit reached"
    finally:
        settings.max_browser_actions = old_actions


def test_check_limits_llm_calls():
    state: InvestigationState = {
        "planner_loop_count": 1,
        "stop_reason": None,
    }
    llm_calls_var.set(15)

    settings = get_settings()
    old_llm = settings.max_llm_calls
    try:
        settings.max_llm_calls = 15
        assert check_limits(state) == "Max LLM calls limit reached"
    finally:
        settings.max_llm_calls = old_llm


def test_check_limits_token_budget():
    state: InvestigationState = {
        "planner_loop_count": 1,
        "stop_reason": None,
    }
    token_usage_var.set(5000)

    settings = get_settings()
    old_budget = settings.token_budget
    try:
        settings.token_budget = 5000
        assert check_limits(state) == "Token budget exhausted"
    finally:
        settings.token_budget = old_budget


def test_llm_calls_enforcement():
    llm = get_llm_provider()

    settings = get_settings()
    old_llm = settings.max_llm_calls
    try:
        settings.max_llm_calls = 1
        llm_calls_var.set(0)
        token_usage_var.set(0)

        from pydantic import BaseModel
        class DummySchema(BaseModel):
            pass

        async def run_test():
            await llm.generate_structured("test", DummySchema)
            return llm_calls_var.get()

        calls = asyncio.run(run_test())
        assert calls == 1

        async def run_two_calls():
            await llm.generate_structured("test", DummySchema)
            await llm.generate_structured("test", DummySchema)

        # Second call raises exception because max_llm_calls is 1
        with pytest.raises(LLMProviderException, match="Max LLM calls limit reached"):
            asyncio.run(run_two_calls())
    finally:
        settings.max_llm_calls = old_llm


def test_planner_stop_on_depth_limit(db_session, investigation_id):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "raw_input": {"business_name": "Depth Test Ltd"},
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 2,
        "research_depth": 2,
        "status": "NORMALIZED",
        "stop_reason": None,
    }

    settings = get_settings()
    old_depth = settings.max_research_depth
    try:
        settings.max_research_depth = 3
        with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
            out = planner_node(state)
        assert out["status"] == "MAX_LOOPS_REACHED"
        assert out["stop_reason"] == "Max research depth reached"
        assert len(out["pending_tasks"]) == 0
    finally:
        settings.max_research_depth = old_depth


def test_browser_node_stop_gracefully(db_session, investigation_id):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    # Setup pending task
    task = GraphTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="GSTIN1",
        objective="Verify GSTIN",
        required_fields=["legal_name"],
        priority=1,
    )
    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": [task],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "browser_actions": 1,
        "browser_tasks_count": 1,
        "status": "PENDING_RESEARCH",
        "stop_reason": None,
    }

    settings = get_settings()
    old_actions = settings.max_browser_actions
    try:
        settings.max_browser_actions = 1
        with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
            out = browser_node(state)
        assert out["status"] == "LIMIT_REACHED"
        assert out["stop_reason"] == "Max browser actions limit reached"
        assert len(out["pending_tasks"]) == 1
    finally:
        settings.max_browser_actions = old_actions


def test_browser_node_skips_when_already_limited(db_session, investigation_id):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "LIMIT_REACHED",
        "stop_reason": "Token budget exhausted",
    }
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
        out = browser_node(state)
    assert out["status"] == "LIMIT_REACHED"
    assert out["stop_reason"] == "Token budget exhausted"


def test_graceful_node_failure_not_broken(db_session, investigation_id):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    state: InvestigationState = {
        "investigation_id": str(investigation_id),
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "status": "NORMALIZED",
        "stop_reason": None,
    }
    with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
        out = discovery_node(state)
    assert out["status"] == "DISCOVERY_COMPLETED"
