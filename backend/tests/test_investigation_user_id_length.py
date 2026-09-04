"""
Regression: `investigations.user_id` was VARCHAR(100), but bearer-token-derived
user identifiers are opaque and can exceed 100 characters, causing
`StringDataRightTruncation` on investigation creation. The column is now Text.
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.db.base import Base
from app.main import app as fastapi_app
from app.models.investigation import Investigation

LONG_TOKEN = "tkn_" + "x" * 300  # 304 chars — well over the old VARCHAR(100) limit


def test_user_id_column_is_unbounded_text():
    col = Investigation.__table__.c.user_id
    assert isinstance(col.type, sa.Text)
    assert getattr(col.type, "length", None) is None  # no length cap -> no truncation
    assert col.nullable is True                        # auth behaviour unchanged
    assert col.index is True                           # still indexed


@pytest.fixture(name="db_session")
def _db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(name="client")
def _client(db_session):
    def _override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


def test_long_bearer_token_creates_investigation(client, db_session):
    resp = client.post(
        "/api/v1/investigations/",
        json={"business_name": "Long Token Co"},
        headers={"Authorization": f"Bearer {LONG_TOKEN}"},
    )
    assert resp.status_code == 201, resp.text
    inv_id = uuid.UUID(resp.json()["id"])

    inv = db_session.get(Investigation, inv_id)
    assert inv is not None
    assert inv.user_id == LONG_TOKEN
    assert len(inv.user_id) == len(LONG_TOKEN) > 100  # full value, not truncated


def test_long_user_id_round_trips_on_postgres():
    """Exercises the real (migrated) Postgres column; skipped if PG is unreachable."""
    from sqlalchemy.exc import OperationalError

    from app.db.session import SessionLocal

    token = "pg_" + "y" * 250
    inv_id = uuid.uuid4()
    try:
        with SessionLocal() as s:
            s.add(
                Investigation(
                    id=inv_id,
                    input_data="{}",
                    raw_input="{}",
                    user_id=token,
                    status="created",
                )
            )
            s.commit()
    except OperationalError as exc:  # no database available
        pytest.skip(f"PostgreSQL not reachable: {exc}")

    try:
        with SessionLocal() as s:
            got = s.get(Investigation, inv_id)
            assert got is not None
            assert got.user_id == token
            assert len(got.user_id) == len(token)
    finally:
        with SessionLocal() as s:
            s.query(Investigation).filter(Investigation.id == inv_id).delete()
            s.commit()
