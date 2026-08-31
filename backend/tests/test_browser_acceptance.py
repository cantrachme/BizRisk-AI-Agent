import pytest
from app.agents.browser import BrowserResearchAgent, detect_human_intervention
from app.graph.state import ResearchTask
from app.core.exceptions import HumanInterventionRequiredException


def make_task(
    task_id="TASK-ACCEPT-001",
    task_type="GST_VERIFICATION",
    target="27ABCDE1234F1Z5",
    preferred_sources=None,
    fallback_sources=None,
    required_fields=None,
):
    return ResearchTask(
        task_id=task_id,
        task_type=task_type,
        target=target,
        objective="Acceptance testing task",
        required_fields=required_fields or ["legal_name", "gst_status", "page_text"],
        priority=1,
        preferred_sources=preferred_sources or ["gst.gov.in"],
        fallback_sources=fallback_sources or ["third_party"],
    )


# 1. Successful page fetch/extraction
def test_browser_successful_fetch_and_extraction():
    html = "<html><head><title>Acme Foods Corp</title></head><body>GST Active</body></html>"
    agent = BrowserResearchAgent(fetcher=lambda url: html)
    task = make_task()
    results = agent.execute(task)

    assert len(results) == 3
    assert results[0].field_value == "Acme Foods Corp"
    assert results[1].field_value == "AVAILABLE"
    assert "GST Active" in results[2].field_value


# 2. No-results handling
def test_browser_no_results_handling():
    html = "<html><head></head><body></body></html>"
    agent = BrowserResearchAgent(fetcher=lambda url: html)
    task = make_task(required_fields=["legal_name", "gst_status"])
    results = agent.execute(task)

    assert len(results) == 2
    assert results[0].field_value == "NOT_FOUND"  # Reject target fallback
    assert results[1].field_value == "UNAVAILABLE"


# 3. Multiple-results handling (candidate entities)
def test_browser_multiple_results_handling():
    html = "<html><head><title>Search Results: Acme Corp</title></head><body>Acme Foods Pvt Ltd, Acme Logistics LLC</body></html>"
    agent = BrowserResearchAgent(fetcher=lambda url: html)
    task = make_task(
        task_type="ENTITY_DISCOVERY",
        target="Acme Corp",
        preferred_sources=["generic_web"],
        required_fields=["candidate_entities"]
    )
    results = agent.execute(task)

    assert len(results) == 1
    assert results[0].field_name == "candidate_entities"
    assert isinstance(results[0].field_value, list)
    assert len(results[0].field_value) >= 1
    assert "Acme" in results[0].field_value[0]["name"]


# 4. Timeout/network failure handling
def test_browser_timeout_and_network_failure_handling():
    def failing_fetcher(url):
        raise TimeoutError("Connection timed out after 30000ms")

    agent = BrowserResearchAgent(fetcher=failing_fetcher)
    task = make_task(required_fields=["legal_name", "gst_status"])
    results = agent.execute(task)

    assert len(results) == 2
    assert results[0].field_value == "NOT_FOUND"
    assert results[1].field_value == "UNAVAILABLE"


# 5. Partial-data handling
def test_browser_partial_data_handling():
    # Page has body text but missing title tag
    html = "<html><body>GST Active Registration Information</body></html>"
    agent = BrowserResearchAgent(fetcher=lambda url: html)
    task = make_task(required_fields=["legal_name", "gst_status", "page_title"])
    results = agent.execute(task)

    assert len(results) == 3
    assert results[0].field_value == "NOT_FOUND"
    assert results[1].field_value == "AVAILABLE"
    assert results[2].field_value is None


# 6. CAPTCHA detection and safe stop
def test_browser_captcha_detection_safe_stop():
    captcha_html = "<html><head><title>Bot Verification</title></head><body>Please solve the captcha below</body></html>"
    agent = BrowserResearchAgent(fetcher=lambda url: captcha_html)
    task = make_task()

    with pytest.raises(HumanInterventionRequiredException) as exc_info:
        agent.execute(task)

    assert exc_info.value.intervention_type == "CAPTCHA"


# 7. Third-party/source fallback
def test_browser_third_party_source_fallback():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><body>Data</body></html>")
    task = make_task(
        preferred_sources=["unknown_source"],
        fallback_sources=["third_party"]
    )
    results = agent.execute(task)

    assert len(results) == 3
    assert results[0].source_name == "Third-Party Source"
    assert results[0].confidence == 0.50


# 8. Conversion into structured evidence with traceable IDs/source metadata
def test_browser_structured_evidence_traceability():
    html = "<html><head><title>Official Registry</title></head><body>Active Record</body></html>"
    agent = BrowserResearchAgent(fetcher=lambda url: html)
    task = make_task(task_id="TASK-888", preferred_sources=["gst.gov.in"])
    results = agent.execute(task)

    for i, res in enumerate(results, start=1):
        assert res.result_id == f"RESULT-TASK-888-{i:03d}"
        assert res.task_id == "TASK-888"
        assert res.source_name == "GST Portal"
        assert res.source_url == "https://www.gst.gov.in"
        assert res.confidence == 0.95
        assert res.retrieved_at is not None
