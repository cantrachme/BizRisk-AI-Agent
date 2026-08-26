from app.graph.workflow import app as graph_app


def test_pipeline_normalizes_discovers_plans_and_executes_research():
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

    assert output["planner_loop_count"] == 2
    assert output["pending_tasks"] == []
    assert output["failed_tasks"] == []

    completed_task_types = {
        task.task_type
        for task in output["completed_tasks"]
    }

    assert completed_task_types == {
        "GST_VERIFICATION",
        "WEBSITE_VERIFICATION",
    }

    result_fields = {
        result.field_name
        for result in output["results"]
    }

    assert "candidate_entities" in result_fields
    assert "legal_name" in result_fields
    assert "gst_status" in result_fields
    assert "website_status" in result_fields

    assert output["status"] == "COMPLETED"
