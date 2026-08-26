import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.graph.state import ResearchResult
from app.models.evidence import Evidence
from app.validation.research import validate_research_result


def save_research_result(
    db: Session,
    result: ResearchResult,
    investigation_id: uuid.UUID,
    commit: bool = True,
) -> Optional[Evidence]:
    """
    Validates a ResearchResult and persists it to the database under the given investigation_id.
    Returns the created Evidence model, or None if the result is invalid.
    """
    validation = validate_research_result(result)
    if not validation.is_valid:
        return None

    # Parse retrieved_at to datetime object
    retrieved_at = result.retrieved_at or ""
    if retrieved_at.endswith("Z"):
        retrieved_at = retrieved_at.replace("Z", "+00:00")

    try:
        retrieved_dt = datetime.fromisoformat(retrieved_at)
    except ValueError:
        retrieved_dt = datetime.now(timezone.utc)

    if retrieved_dt.tzinfo is None:
        retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)

    # Serialize field_value to string if it is not already a string
    val = result.field_value
    if not isinstance(val, str):
        val_str = json.dumps(val)
    else:
        val_str = val

    evidence = Evidence(
        investigation_id=investigation_id,
        research_result_id=result.result_id,
        task_id=result.task_id,
        field_name=result.field_name,
        field_value=val_str,
        source_name=result.source_name,
        source_url=result.source_url,
        retrieved_timestamp=retrieved_dt,
        confidence=result.confidence,
    )
    db.add(evidence)
    if commit:
        db.commit()
        db.refresh(evidence)
    return evidence


def get_evidences_for_investigation(
    db: Session,
    investigation_id: uuid.UUID,
) -> List[Evidence]:
    """
    Retrieves all persisted evidences associated with the given investigation_id.
    """
    return (
        db.query(Evidence)
        .filter(Evidence.investigation_id == investigation_id)
        .all()
    )
