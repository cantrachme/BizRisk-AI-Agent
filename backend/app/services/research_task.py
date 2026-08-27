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
    Updates existing tasks and increments retry counts if retried.
    """
    persisted = []
    for task in tasks:
        existing = (
            db.query(ResearchTaskModel)
            .filter(
                ResearchTaskModel.investigation_id == investigation_id,
                ResearchTaskModel.task_id == task.task_id,
            )
            .first()
        )
        if existing:
            # If the task has already run, increment retry count on schedule
            if existing.completed_at or existing.started_at or existing.status in {"COMPLETED", "FAILED"}:
                existing.retry_count += 1
            existing.status = task.status or "PENDING"
            existing.target = task.target
            existing.objective = task.objective
            existing.started_at = None
            existing.completed_at = None
            existing.error_info = None
            existing.result_info = None
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
