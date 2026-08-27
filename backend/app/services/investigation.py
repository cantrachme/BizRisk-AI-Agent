import json
import uuid
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session
from app.models.investigation import Investigation

def serialize_state(state: Dict[str, Any]) -> str:
    if not state:
        return "{}"
    serialized = {}
    for k, v in state.items():
        if isinstance(v, list):
            new_list = []
            for item in v:
                if hasattr(item, "model_dump"):
                    new_list.append(item.model_dump())
                elif hasattr(item, "dict"):
                    new_list.append(item.dict())
                else:
                    new_list.append(item)
            serialized[k] = new_list
        elif hasattr(v, "model_dump"):
            serialized[k] = v.model_dump()
        elif hasattr(v, "dict"):
            serialized[k] = v.dict()
        else:
            serialized[k] = v
    return json.dumps(serialized)

def deserialize_state(state_json: str) -> Dict[str, Any]:
    if not state_json:
        return {}
    from app.graph.state import ResearchTask, ResearchResult
    
    data = json.loads(state_json)
    
    # Convert task lists back to Pydantic objects
    for task_list_key in ["pending_tasks", "completed_tasks", "failed_tasks"]:
        if task_list_key in data and isinstance(data[task_list_key], list):
            data[task_list_key] = [ResearchTask(**t) for t in data[task_list_key]]
            
    # Convert results back to Pydantic objects
    if "results" in data and isinstance(data["results"], list):
        data["results"] = [ResearchResult(**r) for r in data["results"]]
        
    return data

def recover_investigation_state(db: Session, investigation_id: uuid.UUID) -> Dict[str, Any]:
    inv = db.get(Investigation, investigation_id)
    if not inv:
        return {}

    state = {}
    if inv.persistent_graph_state:
        try:
            state = deserialize_state(inv.persistent_graph_state)
        except Exception:
            pass

    if not state:
        # Fallback reconstruction for backward compatibility
        from app.services.research_task import get_research_tasks_for_investigation
        from app.services.evidence import get_evidences_for_investigation
        from app.graph.state import ResearchTask as GraphTask, ResearchResult as GraphResult

        tasks_db = get_research_tasks_for_investigation(db, investigation_id)
        evidences_db = get_evidences_for_investigation(db, investigation_id)

        pending_tasks = []
        completed_tasks = []
        failed_tasks = []

        def get_fields_for_task_type(task_type: str) -> list:
            if task_type == "ENTITY_DISCOVERY":
                return ["candidate_entities"]
            elif task_type == "GST_VERIFICATION":
                return ["legal_name", "gst_status", "registered_address", "business_activity"]
            elif task_type == "MCA_VERIFICATION":
                return ["legal_name", "company_status", "incorporation_date", "registered_address"]
            elif task_type == "WEBSITE_VERIFICATION":
                return ["website_status", "contact_address", "established_year"]
            else:
                return ["page_text"]

        def get_preferred_sources_for_task_type(task_type: str) -> list:
            if task_type == "GST_VERIFICATION":
                return ["gst.gov.in"]
            elif task_type == "MCA_VERIFICATION":
                return ["mca.gov.in"]
            elif task_type == "WEBSITE_VERIFICATION":
                return ["company_website"]
            else:
                return ["generic_web"]

        def get_fallback_sources_for_task_type(task_type: str) -> list:
            if task_type in {"GST_VERIFICATION", "MCA_VERIFICATION"}:
                return ["third_party"]
            elif task_type == "WEBSITE_VERIFICATION":
                return ["generic_web"]
            else:
                return []

        def get_priority_for_task_type(task_type: str) -> int:
            if task_type == "WEBSITE_VERIFICATION":
                return 2
            return 1

        for t in tasks_db:
            gt = GraphTask(
                task_id=t.task_id,
                task_type=t.task_type,
                target=t.target,
                objective=t.objective,
                status=t.status,
                priority=get_priority_for_task_type(t.task_type),
                required_fields=get_fields_for_task_type(t.task_type),
                preferred_sources=get_preferred_sources_for_task_type(t.task_type),
                fallback_sources=get_fallback_sources_for_task_type(t.task_type),
            )
            if t.status == "COMPLETED":
                completed_tasks.append(gt)
            elif t.status == "FAILED":
                failed_tasks.append(gt)
            else:
                pending_tasks.append(gt)

        results = []
        for ev in evidences_db:
            results.append(
                GraphResult(
                    result_id=ev.research_result_id or f"RESULT-{ev.task_id}-001",
                    task_id=ev.task_id,
                    field_name=ev.field_name,
                    field_value=ev.field_value,
                    source_name=ev.source_name,
                    source_url=ev.source_url,
                    retrieved_at=ev.retrieved_timestamp.isoformat() if ev.retrieved_timestamp else "",
                    confidence=ev.confidence,
                )
            )

        raw_input = json.loads(inv.input_data) if inv.input_data else {}
        normalized_input = json.loads(inv.normalized_input) if inv.normalized_input else {}

        state = {
            "investigation_id": str(investigation_id),
            "raw_input": raw_input,
            "normalized_input": normalized_input,
            "pending_tasks": pending_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "results": results,
            "planner_loop_count": inv.retry_count,
            "status": inv.status or "created",
            "research_depth": 0,
            "browser_actions": 0,
            "browser_tasks_count": 0,
            "llm_calls": 0,
            "token_usage": 0,
            "stop_reason": None,
        }

    return state
