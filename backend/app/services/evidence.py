import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.graph.state import ResearchResult
from app.models.evidence import Evidence
from app.validation.research import validate_research_result


def save_research_results(
    db: Session,
    results: List[ResearchResult],
    investigation_id: uuid.UUID,
) -> List[Evidence]:
    """
    Validates a list of ResearchResults and persists them to the database in a single transaction.
    Returns the created Evidence models.
    """
    evidences = []
    for result in results:
        validation = validate_research_result(result)
        if not validation.is_valid:
            continue

        retrieved_at = result.retrieved_at or ""
        if retrieved_at.endswith("Z"):
            retrieved_at = retrieved_at.replace("Z", "+00:00")

        # Parse retrieved_at to datetime object, ensuring it is timezone-aware
        try:
            retrieved_dt = datetime.fromisoformat(retrieved_at)
            if retrieved_dt.tzinfo is None:
                retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)
            else:
                retrieved_dt = retrieved_dt.astimezone(timezone.utc)
        except ValueError:
            retrieved_dt = datetime.now(timezone.utc)

        # Serialize field_value to string if it is not already a string
        val = result.field_value
        if not isinstance(val, str):
            val_str = json.dumps(val)
        else:
            val_str = val

        from app.models.research_task import ResearchTask as ResearchTaskModel
        task_db = (
            db.query(ResearchTaskModel)
            .filter(
                ResearchTaskModel.investigation_id == investigation_id,
                ResearchTaskModel.task_id == result.task_id,
            )
            .first()
        )
        task_id_val = task_db.id if task_db else None

        evidence = Evidence(
            investigation_id=investigation_id,
            research_result_id=result.result_id,
            task_id=result.task_id,
            research_task_id=task_id_val,
            field_name=result.field_name,
            field_value=val_str,
            source_name=result.source_name,
            source_url=result.source_url,
            retrieved_timestamp=retrieved_dt,
            confidence=result.confidence,
            verification_status="UNVERIFIED",
        )
        db.add(evidence)
        evidences.append(evidence)

    if evidences:
        db.commit()
        for ev in evidences:
            db.refresh(ev)

    return evidences


def save_research_result(
    db: Session,
    result: ResearchResult,
    investigation_id: uuid.UUID,
) -> Optional[Evidence]:
    """
    Validates a ResearchResult and persists it to the database under the given investigation_id.
    Returns the created Evidence model, or None if the result is invalid.
    """
    saved = save_research_results(db, [result], investigation_id)
    return saved[0] if saved else None


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
