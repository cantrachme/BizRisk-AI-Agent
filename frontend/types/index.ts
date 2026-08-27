export type RiskLevel = 'LOW' | 'MODERATE' | 'HIGH' | 'VERY_HIGH' | 'UNKNOWN';

export interface InvestigationListItem {
  id: string;
  status: string;
  current_node: string | null;
  created_at: string | null;
}

export interface InvestigationDetail {
  id: string;
  status: string;
  input: {
    business_name?: string;
    gstin?: string;
    cin?: string;
    website?: string;
    location?: string;
    [key: string]: unknown;
  };
  current_node: string | null;
  retry_count: number;
  risk_score: number | null;
  risk_level: RiskLevel | null;
  resolved_entity_id: string | null;
  entity_confidence: number;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvidenceItem {
  id: string;
  investigation_id: string;
  research_result_id: string;
  task_id: string;
  field_name: string;
  field_value: string;
  source_name: string;
  source_url: string | null;
  retrieved_timestamp: string | null;
  confidence: number;
  created_timestamp: string | null;
}

export interface RiskSignal {
  category: string;
  code: string;
  severity: string;
  description: string;
  evidence_ids: string[];
  confidence: number;
  risk_weight: number;
}

export interface RiskAnalysis {
  overall_risk: {
    score: number;
    level: string;
  };
  category_scores: {
    identity: number;
    registration: number;
    compliance: number;
    consistency: number;
    operational: number;
    activity: number;
    public_footprint: number;
    [key: string]: number;
  };
  risk_signals: RiskSignal[];
}

export interface HistoricalReport {
  id: string;
  investigation_id: string;
  version: number;
  report: {
    entity?: Record<string, unknown>;
    entity_confidence?: number;
    overall_risk?: {
      score: number;
      level: string;
    };
    category_scores?: Record<string, number>;
    major_findings?: Record<string, unknown>[];
    recommendation?: string;
    evidence_summary?: Record<string, unknown>[];
    meta?: {
      rule_version?: string;
      report_version?: string;
      prompt_version?: Record<string, string>;
      model_version?: string;
      generated_at?: string;
    };
    [key: string]: unknown;
  };
  qa_status: 'PENDING' | 'PASS' | 'FAIL';
  created_at: string | null;
}

export interface PendingInterventionTask {
  id: string;
  task_id: string;
  task_type: string;
  target: string;
  objective: string;
  status: string;
  intervention_type: 'CAPTCHA' | 'OTP' | 'LOGIN_REQUIRED' | null;
  intervention_reason: string | null;
}

export interface HumanInterventionStatus {
  investigation_id: string;
  status: string;
  pending_tasks: PendingInterventionTask[];
}
