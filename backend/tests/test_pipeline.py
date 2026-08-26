from app.graph.workflow import app as graph_app


def test_pipeline_normalizes_discovers_and_plans():
    state = {
        "investigation_id": "INV-PIPELINE-001",
        "raw_input": {
            "business_name": "  ABC Foods Pvt Ltd ",
            "gstin": "27abcde1234f1z5",
            "website": "abcfoods.in",
            "location": "  Noida ",
        },
        "normalized_input": {},
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "planner_loop_count": 0,
        "status": "CREATED",
    }

    output = graph_app.invoke(state)

    assert output["normalized_input"]["business_name"] == "ABC FOODS PVT LTD"
    assert output["normalized_input"]["gstin"] == "27ABCDE1234F1Z5"
    assert len(output["results"]) == 1
    assert output["results"][0].field_name == "candidate_entities"

    task_types = {
        task.task_type
        for task in output["pending_tasks"]
    }

    assert "GST_VERIFICATION" in task_types
    assert "WEBSITE_VERIFICATION" in task_types
    assert output["status"] == "PENDING_RESEARCH"
