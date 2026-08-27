import pytest
import uuid
from fastapi import Request, Depends
from sqlalchemy.orm import Session
from app.main import app
from app.api.auth import get_current_user_id
from app.db import get_db
from app.models import Investigation

@pytest.fixture(autouse=True)
def override_auth_for_tests():
    def mock_get_current_user_id(
        request: Request,
        db: Session = Depends(get_db),
    ) -> str | None:
        # Honor explicit Authorization header if provided (useful for our security isolation tests)
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header.split("Bearer ")[1].strip()

        # Otherwise, fall back to self-adapting user_id retrieval using the active db session
        path_params = request.path_params
        inv_id_str = path_params.get("investigation_id")
        if inv_id_str:
            try:
                inv_id = uuid.UUID(inv_id_str)
                inv = db.get(Investigation, inv_id)
                if inv:
                    return inv.user_id
            except Exception:
                pass
        return "user-123"

    app.dependency_overrides[get_current_user_id] = mock_get_current_user_id
    yield
    app.dependency_overrides.clear()
