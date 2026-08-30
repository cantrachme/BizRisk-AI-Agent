import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.graph.state import ResearchResult
from app.models.evidence import Evidence
from app.validation.research import validate_research_result


def is_evidence_fresh(retrieved_timestamp: datetime, field_name: str) -> bool:
    """
    Checks if evidence is fresh according to Settings configuration thresholds.
    """
    from app.core.config import get_settings
    settings = get_settings()

    fn_lower = field_name.lower()
    if "gst" in fn_lower:
        limit_days = settings.evidence_freshness_gst_days
    elif "mca" in fn_lower or "company" in fn_lower:
        limit_days = settings.evidence_freshness_mca_days
    elif "website" in fn_lower:
        limit_days = settings.evidence_freshness_website_days
    else:
        limit_days = settings.evidence_freshness_default_days

    # Normalize tz info
    if retrieved_timestamp.tzinfo is None:
        retrieved_timestamp = retrieved_timestamp.replace(tzinfo=timezone.utc)
    else:
        retrieved_timestamp = retrieved_timestamp.astimezone(timezone.utc)

    delta = datetime.now(timezone.utc) - retrieved_timestamp
    return delta.days < limit_days


def save_research_results(
    db: Session,
    results: List[ResearchResult],
    investigation_id: uuid.UUID,
) -> List[Evidence]:
    """
    Validates a list of ResearchResults and persists them to the database in a single transaction.
    Returns the created/updated Evidence models. Prevents duplicate evidence rows and stale overrides.
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

        # Normalize source identity (stale/deduplication check)
        norm_source = str(result.source_name).strip().lower()

        # Check existing Evidence in DB for the same investigation, field, normalized source name, and research_result_id
        existing_ev = (
            db.query(Evidence)
            .filter(
                Evidence.investigation_id == investigation_id,
                Evidence.field_name == result.field_name,
                func.lower(func.trim(Evidence.source_name)) == norm_source,
                Evidence.research_result_id == result.result_id,
            )
            .first()
        )

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

        if existing_ev:
            # Stale evidence protection check
            existing_dt = existing_ev.retrieved_timestamp
            if existing_dt.tzinfo is None:
                existing_dt = existing_dt.replace(tzinfo=timezone.utc)
            else:
                existing_dt = existing_dt.astimezone(timezone.utc)

            if retrieved_dt < existing_dt:
                # Arriving result is older. Do not overwrite newer valid evidence.
                evidences.append(existing_ev)
                continue
            elif retrieved_dt == existing_dt:
                # Equal timestamp - overwrite in place to avoid duplicates
                existing_ev.field_value = val_str
                existing_ev.confidence = result.confidence
                if task_id_val:
                    existing_ev.research_task_id = task_id_val
                    existing_ev.task_id = result.task_id
                evidences.append(existing_ev)
                continue
            else:
                # Arriving result is newer. Overwrite/update.
                existing_ev.field_value = val_str
                existing_ev.retrieved_timestamp = retrieved_dt
                existing_ev.confidence = result.confidence
                existing_ev.verification_status = "UNVERIFIED"
                if task_id_val:
                    existing_ev.research_task_id = task_id_val
                    existing_ev.task_id = result.task_id
                evidences.append(existing_ev)
        else:
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


def get_cached_source_result(
    db: Session,
    task_type: str,
    target: str,
    objective: str,
    field_name: str,
    source_name: str,
) -> Optional[Evidence]:
    """
    Finds a fresh persisted Evidence record matching the task parameters, field name, and source name.
    Different sources do not collide. Ordering ensures the newest evidence is checked first.
    """
    from app.models.research_task import ResearchTask as ResearchTaskModel

    # Normalize task parameters
    norm_task_type = str(task_type).strip().upper()
    norm_target = str(target).strip().lower()
    norm_objective = str(objective).strip().lower()
    norm_source = str(source_name).strip().lower()
    source_names = [norm_source]
    if norm_source == "gst.gov.in":
        source_names.append("gst portal")
    elif norm_source == "gst portal":
        source_names.append("gst.gov.in")
    elif norm_source == "mca.gov.in":
        source_names.append("mca portal")
    elif norm_source == "mca portal":
        source_names.append("mca.gov.in")

    evs = (
        db.query(Evidence)
        .outerjoin(ResearchTaskModel, Evidence.research_task_id == ResearchTaskModel.id)
        .filter(
            Evidence.field_name == field_name,
            func.lower(func.trim(Evidence.source_name)).in_(source_names),
        )
        .order_by(Evidence.retrieved_timestamp.desc())
        .all()
    )

    for ev in evs:
        # Check task match if linked
        if ev.research_task:
            if (
                ev.research_task.task_type.strip().upper() != norm_task_type
                or ev.research_task.target.strip().lower() != norm_target
                or ev.research_task.objective.strip().lower() != norm_objective
            ):
                continue

        # Verify freshness of the cached evidence
        if is_evidence_fresh(ev.retrieved_timestamp, field_name):
            return ev

    return None
