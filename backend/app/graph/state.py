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
    allowed_domains: Optional[List[str]] = Field(default=None)
    status: str = "PENDING"  # PENDING, COMPLETED, FAILED, SOURCE_UNAVAILABLE, BLOCKED
    evidence_status: Optional[str] = None  # COMPLETED_WITH_EVIDENCE, COMPLETED_NO_EVIDENCE, SOURCE_UNAVAILABLE, BLOCKED

class ResearchResult(BaseModel):
    result_id: str
    task_id: str
    field_name: str
    field_value: Any
    source_name: str
    source_url: Optional[str] = None
    retrieved_at: str
    confidence: float
    evidence_basis: Optional[str] = None
    verification_status: Optional[str] = "UNVERIFIED"  # VERIFIED, UNVERIFIED, NOT_FOUND, SOURCE_UNAVAILABLE, BLOCKED, TIMEOUT, REJECTED
    authority_tier: Optional[int] = None  # 1 (Govt), 2 (Company), 3 (Reputable Registry), 4 (Discovery), 5 (Unrelated)
    rejection_reason: Optional[str] = None

class InvestigationState(TypedDict):
    investigation_id: str
    raw_input: Dict[str, Any]
    normalized_input: Dict[str, Any]
    identifier_provenance: Optional[Dict[str, str]]
    pending_tasks: List[ResearchTask]
    completed_tasks: List[ResearchTask]
    failed_tasks: List[ResearchTask]
    results: List[ResearchResult]
    resolved_entity: Optional[Dict[str, Any]]
    entity_confidence: float
    entity_resolution_status: str
    planner_loop_count: int
    status: str
    overall_risk: Optional[Dict[str, Any]]
    category_scores: Optional[Dict[str, Any]]
    risk_signals: Optional[List[Dict[str, Any]]]
    reason_codes: Optional[List[str]]
    source_limitations: Optional[List[Dict[str, Any]]]
    report: Optional[Dict[str, Any]]
    qa_result: Optional[Dict[str, Any]]
    qa_loop_count: int
    research_depth: int
    browser_actions: int
    browser_tasks_count: int
    llm_calls: int
    token_usage: int
    stop_reason: Optional[str]

MAX_PLANNER_LOOPS = 3
