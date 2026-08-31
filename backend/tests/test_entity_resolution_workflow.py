from app.graph.workflow import app as graph_app
from unittest import mock


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
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><body>solve the captcha below</body></html>"
        elif "duckduckgo.com" in url:
            return """
            <html>
            <head><title>Search Results</title></head>
            <body>
              <a href="https://www.zaubacorp.com/company/ABC-FOODS">Zauba link</a>
            </body>
            </html>
            """
        elif "zaubacorp.com" in url:
            return """
            <html>
            <head><title>ABC Foods Pvt Ltd</title></head>
            <body>
              GSTIN of company is 27ABCDE1234F1Z5.
              Address is Noida.
              Active status.
            </body>
            </html>
            """
        return "<html><body>Empty</body></html>"

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
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
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value="<html><body>No results</body></html>"):
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
