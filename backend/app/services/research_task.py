import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.graph.state import ResearchTask
from app.models.research_task import ResearchTask as ResearchTaskModel


def save_research_tasks(
    db: Session,
    tasks: List[ResearchTask],
    investigation_id: uuid.UUID,
) -> List[ResearchTaskModel]:
    """
    Saves a list of graph ResearchTask schemas to the persistent database.
    Prevents duplicate ResearchTask rows for the same investigation_id, task_type, target, and objective.
    Updates existing tasks and increments retry counts if retried.
    """
    from sqlalchemy import func
    persisted = []
    for task in tasks:
        norm_task_type = str(task.task_type).strip().upper()
        norm_target = str(task.target).strip().lower()
        norm_objective = str(task.objective).strip().lower()

        # Find existing task based on normalized task identity (TRD Quality Phase 1)
        existing = (
            db.query(ResearchTaskModel)
            .filter(
                ResearchTaskModel.investigation_id == investigation_id,
                func.upper(func.trim(ResearchTaskModel.task_type)) == norm_task_type,
                func.lower(func.trim(ResearchTaskModel.target)) == norm_target,
                func.lower(func.trim(ResearchTaskModel.objective)) == norm_objective,
            )
            .first()
        )
        if existing:
            # If the task has already run, increment retry count on schedule
            if existing.completed_at or existing.started_at or existing.status in {"COMPLETED", "FAILED"}:
                existing.retry_count += 1
            existing.status = task.status or "PENDING"
            # Update the graph task_id reference to ensure correct graph execution tracking
            existing.task_id = task.task_id
            existing.started_at = None
            existing.completed_at = None
            existing.error_info = None
            existing.result_info = None
            existing.intervention_type = None
            existing.intervention_reason = None
            persisted.append(existing)
        else:
            new_task = ResearchTaskModel(
                investigation_id=investigation_id,
                task_id=task.task_id,
                task_type=task.task_type,
                target=task.target,
                objective=task.objective,
                status=task.status or "PENDING",
                retry_count=0,
            )
            db.add(new_task)
            persisted.append(new_task)

    db.commit()
    for p in persisted:
        db.refresh(p)
    return persisted


def update_research_task_status(
    db: Session,
    investigation_id: uuid.UUID,
    task_id: str,
    status: str,
    error: Optional[str] = None,
    result: Optional[str] = None,
    intervention_type: Optional[str] = None,
    intervention_reason: Optional[str] = None,
) -> Optional[ResearchTaskModel]:
    """
    Updates the execution status, attempt timestamps, and metadata of a persistent research task.
    """
    task = (
        db.query(ResearchTaskModel)
        .filter(
            ResearchTaskModel.investigation_id == investigation_id,
            ResearchTaskModel.task_id == task_id,
        )
        .first()
    )
    if not task:
        return None

    task.status = status
    now = datetime.now(timezone.utc)
    if status == "STARTED":
        task.started_at = now
    elif status == "COMPLETED":
        task.completed_at = now
        if result:
            task.result_info = result
    elif status == "FAILED":
        task.completed_at = now
        if error:
            task.error_info = error
    elif status == "HUMAN_INTERVENTION_REQUIRED":
        if error:
            task.error_info = error
        if intervention_type:
            task.intervention_type = intervention_type
        if intervention_reason:
            task.intervention_reason = intervention_reason

    db.commit()
    db.refresh(task)
    return task


def get_research_tasks_for_investigation(
    db: Session,
    investigation_id: uuid.UUID,
) -> List[ResearchTaskModel]:
    """
    Retrieves all persisted research tasks for an investigation.
    """
    return (
        db.query(ResearchTaskModel)
        .filter(ResearchTaskModel.investigation_id == investigation_id)
        .all()
    )
