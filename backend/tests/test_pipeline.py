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

    # Mock _fetch_page to return valid mocked HTML responses for both GST and website verification
    from unittest import mock
    def mock_fetcher(url: str) -> str:
        url_lower = url.lower()
        if "gst.gov.in" in url_lower:
            return """
            <html>
              <head><title>GST Details</title></head>
              <body>
                <div>GSTIN: 27ABCDE1234F1Z5</div>
                <div>Legal Name: ABC FOODS PRIVATE LIMITED</div>
                <div>GST status: Active</div>
                <div>Registered Address: Noida, UP</div>
              </body>
            </html>
            """
        else:
            return """
            <html>
              <head><title>ABC Foods Website</title></head>
              <body>
                <h1>ABC Foods Private Limited Noida</h1>
                <p>Welcome to our official website</p>
              </body>
            </html>
            """

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
        output = graph_app.invoke(state)

    assert output["normalized_input"]["business_name"] == "ABC FOODS PVT LTD"
    assert output["normalized_input"]["gstin"] == "27ABCDE1234F1Z5"

    assert output["planner_loop_count"] == 1
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

    assert output["status"] in {"ENTITY_RESOLVED", "COMPLETED"}
