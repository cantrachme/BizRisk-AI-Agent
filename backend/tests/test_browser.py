from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask


def make_task(
    task_type="GST_VERIFICATION",
    preferred_sources=None,
    fallback_sources=None,
    required_fields=None,
):
    return ResearchTask(
        task_id="TASK-001",
        task_type=task_type,
        target="27ABCDE1234F1Z5",
        objective="Test research task",
        required_fields=required_fields or ["legal_name", "gst_status"],
        priority=1,
        preferred_sources=preferred_sources or [],
        fallback_sources=fallback_sources or [],
    )


def test_preferred_source_is_selected():
    task = make_task(
        preferred_sources=["gst.gov.in"],
        fallback_sources=["third_party"],
    )

    results = BrowserResearchAgent().execute(task)

    assert len(results) == 2
    assert results[0].source_name == "GST Portal"
    assert results[0].source_url == "https://www.gst.gov.in"
    assert results[0].confidence == 0.95


def test_fallback_source_is_used():
    task = make_task(
        fallback_sources=["third_party"],
    )

    results = BrowserResearchAgent().execute(task)

    assert len(results) == 2
    assert results[0].source_name == "Third-Party Source"
    assert results[0].confidence == 0.50


def test_entity_discovery_returns_candidate_entities():
    task = make_task(
        task_type="ENTITY_DISCOVERY",
        preferred_sources=["generic_web"],
        required_fields=["candidate_entities"],
    )

    results = BrowserResearchAgent().execute(task)

    assert len(results) == 1
    assert results[0].field_name == "candidate_entities"
    assert isinstance(results[0].field_value, list)


def test_unsupported_task_returns_empty_results():
    task = make_task(
        task_type="UNKNOWN_TASK",
        preferred_sources=["generic_web"],
    )

    assert BrowserResearchAgent().execute(task) == []


def test_missing_source_returns_empty_results():
    task = make_task(
        preferred_sources=["unknown_source"],
    )

    assert BrowserResearchAgent().execute(task) == []


def test_result_ids_are_deterministic():
    task = make_task(preferred_sources=["gst.gov.in"])

    results = BrowserResearchAgent().execute(task)

    assert results[0].result_id == "RESULT-TASK-001-001"
    assert results[1].result_id == "RESULT-TASK-001-002"
