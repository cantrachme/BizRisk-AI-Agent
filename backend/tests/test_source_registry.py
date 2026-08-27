import pytest
import uuid
import json
from datetime import datetime, timezone
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.investigation import Investigation
from app.models.entity import Entity
from app.models.candidate_entity import CandidateEntity
from app.models.browser_session import BrowserSession
from app.models.browser_artifact import BrowserArtifact
from app.models.source_registry import SourceRegistry
from app.models.risk_signal import RiskSignal
from app.models.report import Report

from app.services.source_registry import (
    create_source,
    get_source,
    get_source_by_name,
    list_sources,
    update_source,
    enable_source,
    get_preferred_sources,
    populate_default_sources,
)
from app.agents.planner import PlannerAgent
from app.agents.browser import BrowserResearchAgent
from app.graph.state import InvestigationState, ResearchTask


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
    inv = Investigation(
        input_data='{"business_name": "Test LLC"}',
        status="created"
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


# --- 1. MODEL TESTS ---

def test_models_creation_and_relationships(db_session, investigation_id):
    # Create Entity
    entity = Entity(
        canonical_name="ACME Corp",
        gstin="27ABCDE1234F1Z5",
        cin="U12345MH2026PTC123456",
        registered_address="Mumbai, India",
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)
    
    assert entity.id is not None
    assert entity.canonical_name == "ACME Corp"
    assert entity.created_at is not None

    # Link Entity to Investigation
    inv = db_session.get(Investigation, investigation_id)
    inv.resolved_entity_id = entity.id
    db_session.commit()
    db_session.refresh(inv)
    assert inv.resolved_entity.canonical_name == "ACME Corp"

    # Create CandidateEntity
    cand = CandidateEntity(
        investigation_id=investigation_id,
        name="ACME Candidate",
        gstin="27ABCDE1234F1Z5",
        confidence=0.90,
        status="unverified",
    )
    db_session.add(cand)
    db_session.commit()
    db_session.refresh(cand)

    assert cand.id is not None
    assert len(inv.candidate_entities) == 1
    assert inv.candidate_entities[0].name == "ACME Candidate"

    # Create BrowserSession
    session = BrowserSession(
        investigation_id=investigation_id,
        task_id="TASK-001",
        domain="gst.gov.in",
        status="success",
        action_count=5,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    assert session.id is not None
    assert len(inv.browser_sessions) == 1
    assert inv.browser_sessions[0].domain == "gst.gov.in"

    # Create BrowserArtifact
    art = BrowserArtifact(
        investigation_id=investigation_id,
        task_id="TASK-001",
        url="https://www.gst.gov.in",
        content_hash="abc123hash",
        storage_location="/artifacts/abc123hash.html",
    )
    db_session.add(art)
    db_session.commit()
    db_session.refresh(art)

    assert art.id is not None
    assert len(inv.browser_artifacts) == 1
    assert inv.browser_artifacts[0].content_hash == "abc123hash"

    # Test Cascade Delete
    db_session.delete(inv)
    db_session.commit()

    # The candidate, browser sessions, and artifacts must be deleted due to cascade
    assert db_session.get(CandidateEntity, cand.id) is None
    assert db_session.get(BrowserSession, session.id) is None
    assert db_session.get(BrowserArtifact, art.id) is None
    # Entity should remain (ondelete='SET NULL' or standalone)
    assert db_session.get(Entity, entity.id) is not None


# --- 2. SOURCE REGISTRY TESTS ---

def test_source_registry_crud_and_deterministic_selection(db_session):
    # CRUD
    source = create_source(
        db_session,
        name="custom_gst",
        type="GST_VERIFICATION",
        domain="https://custom.gst.gov.in",
        enabled=True,
        priority=3,
        config={"confidence": 0.99}
    )
    assert source.id is not None
    assert source.name == "custom_gst"
    
    # Retrieve
    fetched = get_source(db_session, source.id)
    assert fetched.domain == "https://custom.gst.gov.in"

    fetched_name = get_source_by_name(db_session, "custom_gst")
    assert fetched_name.priority == 3

    # Update
    update_source(db_session, source.id, priority=1, config={"confidence": 0.98})
    db_session.refresh(source)
    assert source.priority == 1
    assert json.loads(source.config_json)["confidence"] == 0.98

    # Enable / Disable
    enable_source(db_session, source.id, enabled=False)
    db_session.refresh(source)
    assert source.enabled is False

    # Reset/Enable and Seed Default Sources
    enable_source(db_session, source.id, enabled=True)
    populate_default_sources(db_session)
    
    # List sources
    all_sources = list_sources(db_session)
    assert len(all_sources) > 5

    # Test Deterministic Selection by Priority & Name
    # custom_gst has priority 1, gst.gov.in has priority 1, third_party has priority 2
    # Alphabetical order: custom_gst (first), gst.gov.in (second)
    pref, fall = get_preferred_sources(db_session, "GST_VERIFICATION")
    assert pref == ["custom_gst"]
    assert fall[0] == "gst.gov.in"
    assert "third_party" in fall


def test_disabled_source_exclusion(db_session):
    populate_default_sources(db_session)
    
    # Disable gst.gov.in
    gst_source = db_session.query(SourceRegistry).filter_by(name="gst.gov.in", type="GST_VERIFICATION").first()
    update_source(db_session, gst_source.id, enabled=False)

    pref, fall = get_preferred_sources(db_session, "GST_VERIFICATION")
    assert "gst.gov.in" not in pref
    assert "gst.gov.in" not in fall
    assert pref == ["third_party"]


# --- 3. PLANNER INTEGRATION TESTS ---

def test_planner_integration(db_session):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    populate_default_sources(db_session)
    
    # 1. Custom priority selection inside planner
    # Make third_party priority 1 and gst.gov.in priority 2
    gst_source = db_session.query(SourceRegistry).filter_by(name="gst.gov.in", type="GST_VERIFICATION").first()
    tp_source = db_session.query(SourceRegistry).filter_by(name="third_party", type="GST_VERIFICATION").first()
    
    update_source(db_session, gst_source.id, priority=2)
    update_source(db_session, tp_source.id, priority=1)

    state: InvestigationState = {
        "investigation_id": "INV-111",
        "raw_input": {"gstin": "27ABCDE1234F1Z5"},
        "normalized_input": {"gstin": "27ABCDE1234F1Z5"},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
    }

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
        planner = PlannerAgent()
        new_tasks = planner.plan(state)

    assert len(new_tasks) == 1
    task = new_tasks[0]
    assert task.task_type == "GST_VERIFICATION"
    # preferred_sources must be third_party since priority is 1
    assert task.preferred_sources == ["third_party"]
    assert task.fallback_sources == ["gst.gov.in"]

    # 2. Test Planner fallback continues working when DB session raises exception
    with mock.patch("app.db.session.SessionLocal", side_effect=Exception("DB down")):
        planner_fallback = PlannerAgent()
        fallback_tasks = planner_fallback.plan(state)
    assert len(fallback_tasks) == 1
    assert fallback_tasks[0].preferred_sources == ["gst.gov.in"]


# --- 4. BROWSER INTEGRATION TESTS ---

def test_browser_integration(db_session):
    class MockSessionLocal:
        def __enter__(self):
            return db_session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    populate_default_sources(db_session)
    
    # Customize GST Portal config/confidence in the registry
    gst_source = db_session.query(SourceRegistry).filter_by(name="gst.gov.in", type="GST_VERIFICATION").first()
    update_source(db_session, gst_source.id, config={"confidence": 0.99})

    task = ResearchTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="27ABCDE1234F1Z5",
        objective="Verify GST",
        required_fields=["legal_name"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=["third_party"],
    )

    with mock.patch("app.db.session.SessionLocal", MockSessionLocal):
        agent = BrowserResearchAgent(fetcher=lambda url: "<html><title>GST</title></html>")
        results = agent.execute(task)

    assert len(results) == 1
    assert results[0].confidence == 0.99
    assert results[0].source_name == "gst.gov.in"

    # Test Browser Fallback defaults when DB fails
    with mock.patch("app.db.session.SessionLocal", side_effect=Exception("DB Down")):
        agent_fallback = BrowserResearchAgent(fetcher=lambda url: "<html><title>GST</title></html>")
        results_fb = agent_fallback.execute(task)
    assert len(results_fb) == 1
    assert results_fb[0].confidence == 0.95  # Fallback module default
