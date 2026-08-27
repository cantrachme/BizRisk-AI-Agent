from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class IntakeOutput(BaseModel):
    business_name: Optional[str] = None
    gstin: Optional[str] = None
    cin: Optional[str] = None
    website: Optional[str] = None
    location: Optional[str] = None

class DiscoveryCandidate(BaseModel):
    business_name: str
    gstin: Optional[str] = None
    cin: Optional[str] = None
    confidence: float
    match_type: str

class DiscoveryOutput(BaseModel):
    candidate_entities: List[DiscoveryCandidate] = []

class ResearchTaskSchema(BaseModel):
    task_id: str
    source_name: str
    query: str
    status: str

class PlannerOutput(BaseModel):
    tasks: List[ResearchTaskSchema]
    investigation_ready_for_resolution: bool
    reasoning_summary: str

class EntityResolutionOutput(BaseModel):
    matched: bool
    match_type: str
    confidence: float
    entity: Dict[str, Any] = {}

class RiskSignalSchema(BaseModel):
    category: str
    code: str
    severity: str
    description: str
    evidence_ids: List[str]
    confidence: float
    risk_weight: float

class OverallRiskSchema(BaseModel):
    score: int
    level: str

class RiskAnalysisOutput(BaseModel):
    overall_risk: OverallRiskSchema
    category_scores: Dict[str, int]
    risk_signals: List[RiskSignalSchema]

class ReportOutput(BaseModel):
    entity: Dict[str, Any]
    entity_confidence: float
    overall_risk: OverallRiskSchema
    category_scores: Dict[str, int]
    major_findings: List[Dict[str, Any]]
    positive_findings: List[Dict[str, Any]] = []
    unverified_information: List[Dict[str, Any]] = []
    recommendation: str
    evidence_summary: List[Dict[str, Any]]
    meta: Dict[str, Any]

class QAOutput(BaseModel):
    status: str
    issues: List[Dict[str, Any]] = []
    evidence_coverage: float
    score_verified: bool
    entity_verified: bool
