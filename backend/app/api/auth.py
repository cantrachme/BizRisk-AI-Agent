import uuid
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Investigation

security = HTTPBearer(auto_error=False)


def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials.strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def get_owned_investigation(
    investigation_id: uuid.UUID,
    current_user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> Investigation:
    investigation = db.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )
    if investigation.user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Investigation not found.",
        )
    return investigation
