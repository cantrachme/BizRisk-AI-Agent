import pytest
from app.graph.workflow import should_continue_after_qa

def test_qa_routing_wrong_risk_score():
    state = {
        "qa_result": {
            "status": "FAIL",
            "issues": [{"type": "WRONG_RISK_SCORE", "finding": "Score mismatch"}]
        },
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state) == "risk_analysis"

def test_qa_routing_report_wording():
    state = {
        "qa_result": {
            "status": "FAIL",
            "issues": [{"type": "REPORT_WORDING", "finding": "Forbidden word used"}]
        },
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state) == "report_generation"

def test_qa_routing_missing_evidence():
    state = {
        "qa_result": {
            "status": "FAIL",
            "issues": [{"type": "MISSING_EVIDENCE", "finding": "Evidence ID missing"}]
        },
        "qa_loop_count": 0
    }
    assert should_continue_after_qa(state) == "planner"
