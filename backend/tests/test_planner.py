import os
import sys
import pytest
from pydantic import ValidationError

# Add backend directory to sys.path to ensure correct imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from app.graph.state import ResearchTask, ResearchResult, InvestigationState
from app.agents.planner import PlannerAgent
from app.graph.workflow import app as graph_app

def test_partial_gstin_creates_verification_task():
    # 1. Start with a partial input containing only a GSTIN
    state: InvestigationState = {
        "investigation_id": "INV-001",
        "raw_input": {"gstin": "09ABCDE1234F1Z5"},
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 0,
        "status": "CREATED"
    }

    planner = PlannerAgent()
    new_tasks = planner.plan(state)

    assert len(new_tasks) == 1
    task = new_tasks[0]
    assert task.task_type == "GST_VERIFICATION"
    assert task.target == "09ABCDE1234F1Z5"
    assert "legal_name" in task.required_fields
    assert "gst_status" in task.required_fields
    assert task.priority == 1
    assert "gst.gov.in" in task.preferred_sources


def test_entity_discovery_when_no_identifiers_present():
    # Start with business name and location, but no GSTIN or CIN
    state: InvestigationState = {
        "investigation_id": "INV-002",
        "raw_input": {"business_name": "Acme Corp", "location": "Delhi"},
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 0,
        "status": "CREATED"
    }

    planner = PlannerAgent()
    new_tasks = planner.plan(state)

    assert len(new_tasks) >= 1
    assert any(t.task_type == "ENTITY_DISCOVERY" for t in new_tasks)
    disc_task = next(t for t in new_tasks if t.task_type == "ENTITY_DISCOVERY")
    assert "Acme Corp" in disc_task.target
    assert "Delhi" in disc_task.target
    assert disc_task.priority == 1


def test_state_updates_in_langgraph_flow():
    initial_state: InvestigationState = {
        "investigation_id": "INV-003",
        "raw_input": {"gstin": "09ABCDE1234F1Z5"},
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 0,
        "status": "CREATED"
    }

    # Run the compiled LangGraph workflow with mocked fetcher to ensure success path
    from unittest import mock
    mock_html = """
    <html>
      <head><title>GST Status</title></head>
      <body>
        <div>GSTIN: 09ABCDE1234F1Z5</div>
        <div>Legal Name: Test Acme Business</div>
        <div>GST status: Active</div>
        <div>Registered Address: 123 Street, Delhi</div>
      </body>
    </html>
    """
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        output_state = graph_app.invoke(initial_state)

    # The full graph executes research across planner passes
    assert output_state["planner_loop_count"] >= 1
    assert len(output_state["pending_tasks"]) == 0
    assert len(output_state["completed_tasks"]) >= 1
    completed_types = {t.task_type for t in output_state["completed_tasks"]}
    assert "GST_VERIFICATION" in completed_types
    assert output_state["status"] in {"ENTITY_RESOLVED", "COMPLETED"}


def test_loop_count_limit_is_enforced():
    # Set the loop counter to 3 initially
    state: InvestigationState = {
        "investigation_id": "INV-004",
        "raw_input": {"gstin": "09ABCDE1234F1Z5"},
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 3,
        "status": "PENDING_RESEARCH"
    }

    # Run the graph; edge routing must trigger __end__ and node must cap it
    output_state = graph_app.invoke(state)

    assert output_state["planner_loop_count"] == 3
    assert output_state["status"] in {"MAX_LOOPS_REACHED", "LIMIT_REACHED", "FAILED", "COMPLETED"}
    assert len(output_state["pending_tasks"]) == 0


def test_invalid_task_data_raises_validation_error():
    # Ensure validation errors are raised for invalid task schema fields
    with pytest.raises(ValidationError):
        # Target is missing (required field)
        ResearchTask(
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            objective="Verify GSTIN",
            required_fields=["legal_name"],
            priority="High"  # Invalid priority type (expects int)
        )


def test_invalid_result_data_raises_validation_error():
    # Ensure validation errors are raised for invalid result schema fields
    with pytest.raises(ValidationError):
        # retrieved_at is missing, confidence is incorrect type
        ResearchResult(
            result_id="RES-001",
            task_id="TASK-001",
            field_name="gst_status",
            field_value="Active",
            source_name="GST Portal",
            confidence="High"  # Invalid confidence type (expects float)
        )
