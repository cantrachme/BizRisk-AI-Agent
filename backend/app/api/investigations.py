import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Investigation
from app.schemas.investigation import InvestigationCreate

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_investigation(
    payload: InvestigationCreate,
    db: Session = Depends(get_db),
) -> dict:
    if not any(
        [
            payload.business_name,
            payload.gstin,
            payload.cin,
            payload.website,
        ]
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one business identifier.",
        )

    investigation = Investigation(
        input_data=payload.model_dump_json(),
    )

    db.add(investigation)
    db.commit()
    db.refresh(investigation)

    return {
        "id": str(investigation.id),
        "status": investigation.status,
    }


@router.get("/{investigation_id}")
def get_investigation(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    investigation = db.get(Investigation, investigation_id)

    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )

    return {
        "id": str(investigation.id),
        "status": investigation.status,
        "input": json.loads(investigation.input_data),
        "created_at": investigation.created_at,
        "updated_at": investigation.updated_at,
    }
