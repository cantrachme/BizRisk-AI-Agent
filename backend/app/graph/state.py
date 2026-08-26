from typing import TypedDict, List, Dict, Any, Optional
from pydantic import BaseModel, Field

class ResearchTask(BaseModel):
    task_id: str
    task_type: str
    target: str
    objective: str
    required_fields: List[str]
    priority: int
    preferred_sources: List[str] = Field(default_factory=list)
    fallback_sources: List[str] = Field(default_factory=list)
    status: str = "PENDING"  # PENDING, COMPLETED, FAILED

class ResearchResult(BaseModel):
    result_id: str
    task_id: str
    field_name: str
    field_value: Any
    source_name: str
    source_url: Optional[str] = None
    retrieved_at: str
    confidence: float

class InvestigationState(TypedDict):
    investigation_id: str
    raw_input: Dict[str, Any]
    normalized_input: Dict[str, Any]
    pending_tasks: List[ResearchTask]
    completed_tasks: List[ResearchTask]
    failed_tasks: List[ResearchTask]
    results: List[ResearchResult]
    resolved_entity: Optional[Dict[str, Any]]
    entity_confidence: float
    entity_resolution_status: str
    planner_loop_count: int
    status: str

MAX_PLANNER_LOOPS = 3
