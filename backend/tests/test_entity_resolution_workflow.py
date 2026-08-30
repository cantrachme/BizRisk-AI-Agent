from app.graph.workflow import app as graph_app


def make_state(raw_input):
    return {
        "investigation_id": "INV-RESOLUTION-001",
        "raw_input": raw_input,
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "resolved_entity": None,
        "entity_confidence": 0.0,
        "entity_resolution_status": "PENDING",
        "planner_loop_count": 0,
        "status": "CREATED",
    }


def test_workflow_resolves_discovered_entity():
    output = graph_app.invoke(
        make_state(
            {
                "business_name": "ABC Foods Pvt Ltd",
                "gstin": "27ABCDE1234F1Z5",
                "website": "abcfoods.in",
                "location": "Noida",
            }
        )
    )

    assert output["resolved_entity"] is not None
    assert output["entity_confidence"] == 1.0
    assert output["entity_resolution_status"] == "EXACT"
    assert output["status"] in {"ENTITY_RESOLVED", "COMPLETED"}


def test_workflow_handles_unresolved_entity():
    output = graph_app.invoke(
        make_state(
            {
                "business_name": "Unknown Business",
                "location": "Mumbai",
            }
        )
    )

    assert output["entity_resolution_status"] in {
        "EXACT",
        "SIMILARITY",
        "NO_MATCH",
    }
