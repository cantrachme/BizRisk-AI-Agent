from datetime import datetime, timezone
import uuid

from app.agents.discovery import DiscoveryAgent
from app.agents.intake import IntakeAgent
from app.agents.planner import PlannerAgent
from app.entity_resolution.resolver import resolve_entity
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

    investigation_id_str = state.get("investigation_id")
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
            from app.db.session import SessionLocal
            from app.services.evidence import save_research_result
            with SessionLocal() as db:
                save_research_result(db, result, investigation_id)
        except ValueError:
            pass

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


def browser_node(state: InvestigationState) -> dict:
    from app.agents.browser import BrowserResearchAgent

    agent = BrowserResearchAgent()

    pending_tasks = state.get("pending_tasks") or []
    existing_completed = state.get("completed_tasks") or []
    existing_failed = state.get("failed_tasks") or []
    existing_results = state.get("results") or []

    completed_tasks = list(existing_completed)
    failed_tasks = list(existing_failed)
    results = list(existing_results)
    new_results = []

    for task in pending_tasks:
        task_results = agent.execute(task)

        if task_results:
            completed_tasks.append(
                task.model_copy(update={"status": "COMPLETED"})
            )
            results.extend(task_results)
            new_results.extend(task_results)
        else:
            failed_tasks.append(
                task.model_copy(update={"status": "FAILED"})
            )

    investigation_id_str = state.get("investigation_id")
    if investigation_id_str and new_results:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
            from app.db.session import SessionLocal
            from app.services.evidence import save_research_results
            with SessionLocal() as db:
                save_research_results(db, new_results, investigation_id)
        except ValueError:
            pass

    return {
        "pending_tasks": [],
        "completed_tasks": completed_tasks,
        "failed_tasks": failed_tasks,
        "results": results,
        "status": "RESEARCH_COMPLETED",
    }



def entity_resolution_node(state: InvestigationState) -> dict:
    normalized_input = state.get("normalized_input") or {}
    results = state.get("results") or []

    candidates = []

    for result in results:
        if result.field_name == "candidate_entities":
            candidates.extend(result.field_value or [])

    resolution = resolve_entity(
        normalized_input,
        candidates,
    )

    if resolution["matched"]:
        return {
            "resolved_entity": resolution["entity"],
            "entity_confidence": resolution["confidence"],
            "entity_resolution_status": resolution["match_type"],
            "status": "ENTITY_RESOLVED",
        }

    return {
        "resolved_entity": resolution["entity"],
        "entity_confidence": resolution["confidence"],
        "entity_resolution_status": resolution["match_type"],
        "status": "ENTITY_UNRESOLVED",
    }


def risk_analysis_node(state: InvestigationState) -> dict:
    investigation_id_str = state.get("investigation_id")
    investigation_id = None
    if investigation_id_str:
        try:
            investigation_id = uuid.UUID(str(investigation_id_str))
        except ValueError:
            pass

    if investigation_id:
        from app.services.risk_analysis import analyze_investigation
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            analysis = analyze_investigation(db, investigation_id)
    else:
        # Fallback to local memory-only calculation for non-UUID / dummy IDs in graph tests
        from app.risk.engine import calculate_risk_analysis
        results = state.get("results") or []
        analysis = calculate_risk_analysis(results)

    return {
        "overall_risk": analysis["overall_risk"],
        "category_scores": analysis["category_scores"],
        "risk_signals": analysis["risk_signals"],
    }
