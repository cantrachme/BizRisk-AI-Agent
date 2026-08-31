import sys
import os
import uuid
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath("backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.source_registry import SourceRegistry
from app.models.investigation_event import InvestigationEvent
from app.models.browser_session import BrowserSession
from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask
import app.db.session

def main():
    # Setup in-memory DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Pre-populate sources in registry
    db = TestingSessionLocal()
    db.add_all([
        SourceRegistry(name="gst.gov.in", type="GST_VERIFICATION", domain="gst.gov.in", enabled=True, config_json='{"confidence": 0.99}'),
        SourceRegistry(name="third_party", type="GST_VERIFICATION", domain="third_party", enabled=True, config_json='{"confidence": 0.50}')
    ])
    db.commit()
    db.close()
    
    # Intercept SessionLocal
    app.db.session.SessionLocal = TestingSessionLocal
    
    # Setup agent and task
    agent = BrowserResearchAgent()
    investigation_id = uuid.uuid4()
    
    task = ResearchTask(
        task_id="TASK-LIVE-WIPRO",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status for Wipro",
        required_fields=["legal_name", "gst_status", "registered_address"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=["third_party"]
    )
    
    print("Executing Browser Research Agent with live internet connection...", flush=True)
    
    try:
        results = agent.execute(task=task, investigation_id=investigation_id)
        print("\n[RESULT] Task succeeded!", flush=True)
    except Exception as ex:
        print(f"\n[RESULT] Task paused/blocked as expected: {ex}", flush=True)
        results = []
        
    # Query database records
    db = TestingSessionLocal()
    attempts = db.query(BrowserSession).order_by(BrowserSession.started_at).all()
    events = db.query(InvestigationEvent).order_by(InvestigationEvent.created_at).all()
    
    print("\n==================================================", flush=True)
    print("1. ACTUAL BROWSER AGENT EVENT SEQUENCE (DB LOG)", flush=True)
    print("==================================================", flush=True)
    for i, e in enumerate(events, start=1):
        # Decode metadata JSON if present
        meta_str = ""
        if e.metadata_json:
            try:
                meta = json.loads(e.metadata_json)
                meta_str = f" | target_id={meta.get('task_id')} source={meta.get('source_name')} url={meta.get('url')} message='{meta.get('message')}'"
            except Exception:
                meta_str = f" | raw={e.metadata_json}"
        print(f"{i:02d}. [{e.event_type}] ({e.status}){meta_str}", flush=True)
        
    print("\n==================================================", flush=True)
    print("2. DETAILED BROWSER ATTEMPTS", flush=True)
    print("==================================================", flush=True)
    for a in attempts:
        print(f"Source/Domain: {a.domain}", flush=True)
        print(f"Outcome Status: {a.status}", flush=True)
        # Parse failure_reason JSON which stores extra metadata
        if a.failure_reason:
            try:
                meta = json.loads(a.failure_reason)
                print(f"Source Name: {meta.get('source_name')}", flush=True)
                print(f"URL Visited: {meta.get('url')}", flush=True)
                print(f"HTTP Result: {meta.get('http_result')}", flush=True)
                print(f"Relevance Result: {meta.get('relevance_result')}", flush=True)
                print(f"Confidence: {meta.get('confidence')}", flush=True)
            except Exception:
                print(f"Extra Metadata: {a.failure_reason}", flush=True)
        print("-" * 30, flush=True)
        
    db.close()

if __name__ == "__main__":
    main()
