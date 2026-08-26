from datetime import datetime, timezone

from app.agents.discovery import DiscoveryAgent
from app.agents.intake import IntakeAgent
from app.agents.planner import PlannerAgent
from app.graph.state import (
    InvestigationState,
    MAX_PLANNER_LOOPS,
    ResearchResult,
)


def intake_node(state: InvestigationState) -> dict:
    normalized_input = IntakeAgent().process(state.get("raw_input") or {})

    return {
        "normalized_input": normalized_input,
        "status": "NORMALIZED",
    }


def discovery_node(state: InvestigationState) -> dict:
    discovery = DiscoveryAgent().process(
        state.get("normalized_input") or {}
    )

    candidates = discovery.get("candidate_entities", [])

    if not candidates:
        return {
            "results": [],
            "status": "DISCOVERY_COMPLETED",
        }

    result = ResearchResult(
        result_id="DISCOVERY-001",
        task_id="DISCOVERY",
        field_name="candidate_entities",
        field_value=candidates,
        source_name="discovery_agent",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        confidence=max(
            candidate.get("confidence", 0.0)
            for candidate in candidates
        ),
    )

    return {
        "results": [result],
        "status": "DISCOVERY_COMPLETED",
    }


def planner_node(state: InvestigationState) -> dict:
    current_loops = state.get("planner_loop_count", 0)

    if current_loops >= MAX_PLANNER_LOOPS:
        return {
            "pending_tasks": [],
            "status": "MAX_LOOPS_REACHED",
        }

    new_tasks = PlannerAgent().plan(state)
    current_loops += 1

    existing_pending = state.get("pending_tasks") or []
    updated_pending = existing_pending + new_tasks

    if current_loops >= MAX_PLANNER_LOOPS:
        status = "MAX_LOOPS_REACHED"
    elif new_tasks:
        status = "PENDING_RESEARCH"
    else:
        status = "COMPLETED"

    return {
        "pending_tasks": updated_pending,
        "planner_loop_count": current_loops,
        "status": status,
    }
