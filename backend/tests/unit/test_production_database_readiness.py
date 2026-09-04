import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.base import Base
from app.services.audit import record_event
from app.main import app


def test_postgres_database_url_normalization():
    s1 = Settings(database_url="postgres://user:pass@localhost:5432/bizrisk")
    assert s1.database_url == "postgresql+psycopg://user:pass@localhost:5432/bizrisk"

    s2 = Settings(database_url="postgresql://user:pass@localhost:5432/bizrisk")
    assert s2.database_url == "postgresql+psycopg://user:pass@localhost:5432/bizrisk"

    s3 = Settings(database_url="sqlite:///:memory:")
    assert s3.database_url == "sqlite:///:memory:"


def test_cors_origins_parsing():
    s = Settings(cors_origins="http://localhost:3000, https://app.bizrisk.ai")
    assert s.cors_origins == ["http://localhost:3000", "https://app.bizrisk.ai"]


def test_production_environment_validation():
    with pytest.raises(ValueError, match="debug must be False in production environment"):
        Settings(environment="production", debug=True)

    # Unauthenticated agent-inspection endpoints must be disabled in production.
    with pytest.raises(ValueError, match="enable_test_endpoints must be False in production"):
        Settings(environment="production", debug=False, enable_test_endpoints=True)

    prod_settings = Settings(
        environment="production", debug=False, enable_test_endpoints=False
    )
    assert prod_settings.environment == "production"
    assert prod_settings.debug is False
    assert prod_settings.enable_test_endpoints is False


def test_db_session_rollback_on_failure():
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()

    # Simulate a failing DB operation inside record_event
    with patch.object(session, "commit", side_effect=RuntimeError("DB Commit Error")):
        record_event(
            db=session,
            investigation_id="00000000-0000-0000-0000-000000000001",
            event_type="TEST_EVENT",
            node="test",
            status="FAILED",
        )

    # Verify session is still functional after rollback
    res = session.execute(Base.metadata.tables["users"].select()).all()
    assert res == []
    session.close()


def test_app_clean_startup_production_config():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"
