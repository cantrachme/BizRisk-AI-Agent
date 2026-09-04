"""
QA / investigation status consistency.

Verifies that when QA genuinely fails and exhausts its retry budget, the
persisted `investigations.status` is FAILED (never COMPLETED), and that a
persisted report's own `qa_status` always ends up PASS/FAIL (never left at the
default PENDING) once QA has actually run for that report version. Exercises
the real `app.services.qa.validate_report` code path (only its DB session is
redirected to the in-memory test database) rather than stubbing QA's verdict,
so the actual production logic that sets `Report.qa_status` is under test.

Generic: no company-specific values, no source-status/business-activity/browser
logic touched.
"""
from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.graph.nodes import qa_node
from app.models.investigation import Investigation
from app.models.report import Report


@pytest.fixture(name="db_session")
def _db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(name="investigation_id")
def _investigation_id(db_session):
    inv = Investigation(input_data='{"business_name": "Sample Enterprises Private Limited"}', status="QA")
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv.id


def _mk_report(db_session, inv_id, version, *, score_matches_engine: bool):
    """A report with no evidence -> the real risk engine sees `insufficient_evidence`
    (score None). Matching the report's own score to that makes Check C/D (score
    consistency) PASS; a deliberate mismatch makes it genuinely FAIL, exercising
    the real `validate_report` verdict rather than a stubbed one."""
    report_dict = {
        "entity": {"business_name": "Sample Enterprises Private Limited"},
        "entity_confidence": 1.0,
        "overall_risk": {
            "score": None if score_matches_engine else 0,
            "level": "INSUFFICIENT_EVIDENCE" if score_matches_engine else "LOW",
        },
        "major_findings": [],
    }
    r = Report(investigation_id=inv_id, version=version, report_json=json.dumps(report_dict), qa_status="PENDING")
    db_session.add(r)
    db_session.commit()
    db_session.refresh(r)
    return r


def _base_state(inv_id, qa_loop_count):
    return {
        "investigation_id": str(inv_id),
        "status": "QA",
        "qa_loop_count": qa_loop_count,
        "results": [],
        "report": {},
        "overall_risk": {},
        "resolved_entity": {},
    }


class _MockSessionLocal:
    """Redirects the module-level `app.db.session.SessionLocal` used inside
    `qa_node` / `validate_report` to the test's in-memory SQLite session,
    without touching any QA verdict logic."""
    def __init__(self, db_session):
        self._db = db_session

    def __call__(self):
        return self

    def __enter__(self):
        return self._db

    def __exit__(self, *a):
        return False


def _run_qa_node(db_session, state):
    with mock.patch("app.db.session.SessionLocal", _MockSessionLocal(db_session)):
        return qa_node(state)


# --------------------------------------------------------------------------- #
# 1 & 2. exhausted QA retries -> investigation FAILED, never COMPLETED, and
#         distinguishable from "not yet executed" / mid-retry
# --------------------------------------------------------------------------- #
def test_qa_retries_exhausted_persists_failed_not_completed(db_session, investigation_id):
    from app.core.config import get_settings
    max_loops = get_settings().max_qa_loops

    loop_count = 0
    for attempt in range(max_loops):
        _mk_report(db_session, investigation_id, version=attempt + 1, score_matches_engine=False)  # forces FAIL
        state = _base_state(investigation_id, loop_count)
        out = _run_qa_node(db_session, state)
        loop_count = out["qa_loop_count"]
        assert out["qa_result"]["status"] == "FAIL"

    db_session.expire_all()
    inv = db_session.get(Investigation, investigation_id)
    assert inv.status == "FAILED", f"expected FAILED after exhausting {max_loops} QA retries, got {inv.status!r}"
    assert inv.status != "COMPLETED"
    assert inv.retry_count == max_loops


def test_qa_single_failure_before_exhaustion_is_failed_qa_not_completed_or_pending(db_session, investigation_id):
    _mk_report(db_session, investigation_id, version=1, score_matches_engine=False)
    state = _base_state(investigation_id, 0)
    out = _run_qa_node(db_session, state)
    assert out["qa_result"]["status"] == "FAIL"
    assert out["status"] == "FAILED_QA"

    db_session.expire_all()
    inv = db_session.get(Investigation, investigation_id)
    # mid-retry: distinguishable from both a finished COMPLETED run and a
    # not-yet-executed one.
    assert inv.status == "FAILED_QA"
    assert inv.status not in {"COMPLETED", "PENDING", None}


def test_qa_pass_persists_completed(db_session, investigation_id):
    _mk_report(db_session, investigation_id, version=1, score_matches_engine=True)
    state = _base_state(investigation_id, 0)
    out = _run_qa_node(db_session, state)
    assert out["qa_result"]["status"] == "PASS"

    db_session.expire_all()
    inv = db_session.get(Investigation, investigation_id)
    assert inv.status == "COMPLETED"


# --------------------------------------------------------------------------- #
# report.qa_status must not remain PENDING once QA has actually run for the
# report version that is current at the time QA executes.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("score_matches_engine", [True, False])
def test_report_qa_status_is_set_after_qa_runs_not_left_pending(db_session, investigation_id, score_matches_engine):
    report = _mk_report(db_session, investigation_id, version=1, score_matches_engine=score_matches_engine)
    assert report.qa_status == "PENDING"

    state = _base_state(investigation_id, 0)
    _run_qa_node(db_session, state)

    db_session.expire_all()
    persisted = db_session.get(Report, report.id)
    assert persisted.qa_status in {"PASS", "FAIL"}
    assert persisted.qa_status != "PENDING"
    assert persisted.qa_status == ("PASS" if score_matches_engine else "FAIL")
