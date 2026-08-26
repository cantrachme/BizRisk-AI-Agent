import uuid
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.risk.engine import calculate_risk_analysis, persist_risk_analysis
from app.services.evidence import get_evidences_for_investigation


def analyze_investigation(
    db: Session,
    investigation_id: uuid.UUID,
) -> Dict[str, Any]:
    """
    Loads persisted evidence for an investigation, runs deterministic risk scoring rules,
    persists/overwrites RiskSignal database models, and returns the calculated analysis results.
    """
    # 1. Load persisted evidence from database
    evidences = get_evidences_for_investigation(db, investigation_id)

    # 2. Compute risk scores, categories, levels, and active signals
    analysis = calculate_risk_analysis(evidences, investigation_id)

    # 3. Overwrite old risk signals in DB
    persist_risk_analysis(db, investigation_id, analysis)

    return analysis
