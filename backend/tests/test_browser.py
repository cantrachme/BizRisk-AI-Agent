from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask


SAMPLE_HTML = """
<html>
    <head>
        <title>ABC Foods Private Limited</title>
    </head>
    <body>
        <h1>ABC Foods Private Limited</h1>
        <p>GST status is active.</p>
        <p>Official company information.</p>
    </body>
</html>
"""


def make_task(
    task_type="GST_VERIFICATION",
    target="27ABCDE1234F1Z5",
    preferred_sources=None,
    fallback_sources=None,
    required_fields=None,
):
    return ResearchTask(
        task_id="TASK-001",
        task_type=task_type,
        target=target,
        objective="Test research task",
        required_fields=required_fields or [
            "legal_name",
            "gst_status",
        ],
        priority=1,
        preferred_sources=preferred_sources or [],
        fallback_sources=fallback_sources or [],
    )


def make_agent():
    return BrowserResearchAgent(
        fetcher=lambda url: SAMPLE_HTML
    )


def test_preferred_source_is_selected():
    task = make_task(
        preferred_sources=["gst.gov.in"],
        fallback_sources=["third_party"],
    )

    results = make_agent().execute(task)

    assert len(results) == 2
    assert results[0].source_name == "GST Portal"
    assert results[0].source_url == "https://www.gst.gov.in"
    assert results[0].confidence == 0.95


def test_fallback_source_is_used():
    task = make_task(
        fallback_sources=["third_party"],
    )

    results = make_agent().execute(task)

    assert len(results) == 2
    assert results[0].source_name == "Third-Party Source"
    assert results[0].confidence == 0.50


def test_real_page_content_is_extracted():
    task = make_task(
        preferred_sources=["gst.gov.in"],
    )

    results = make_agent().execute(task)

    assert results[0].field_value == "ABC Foods Private Limited"
    assert results[1].field_value == "AVAILABLE"


def test_company_website_uses_task_target_url():
    task = make_task(
        task_type="WEBSITE_VERIFICATION",
        target="abcfoods.in",
        preferred_sources=["company_website"],
        required_fields=["page_title"],
    )

    results = make_agent().execute(task)

    assert len(results) == 1
    assert results[0].source_url == "https://abcfoods.in"
    assert results[0].field_value == "ABC Foods Private Limited"


def test_entity_discovery_returns_real_candidate_entities():
    task = make_task(
        task_type="ENTITY_DISCOVERY",
        target="abcfoods.in",
        preferred_sources=["generic_web"],
        required_fields=["candidate_entities"],
    )

    results = make_agent().execute(task)

    assert len(results) == 1
    assert results[0].field_name == "candidate_entities"
    assert results[0].field_value[0]["name"] == (
        "ABC Foods Private Limited"
    )


def test_unsupported_task_returns_empty_results():
    task = make_task(
        task_type="UNKNOWN_TASK",
        preferred_sources=["generic_web"],
    )

    assert make_agent().execute(task) == []


def test_missing_source_returns_empty_results():
    task = make_task(
        preferred_sources=["unknown_source"],
    )

    assert make_agent().execute(task) == []


def test_fetch_failure_returns_fallback_results():
    task = make_task(
        preferred_sources=["gst.gov.in"],
    )

    agent = BrowserResearchAgent(
        fetcher=lambda url: (_ for _ in ()).throw(
            RuntimeError("Network failure")
        )
    )

    results = agent.execute(task)

    assert len(results) == 2
    assert results[0].source_name == "GST Portal"
    assert results[0].field_value == task.target
    assert results[1].field_value == "UNAVAILABLE"


def test_result_ids_are_deterministic():
    task = make_task(
        preferred_sources=["gst.gov.in"],
    )

    results = make_agent().execute(task)

    assert results[0].result_id == "RESULT-TASK-001-001"
    assert results[1].result_id == "RESULT-TASK-001-002"


def test_page_text_is_available():
    task = make_task(
        preferred_sources=["gst.gov.in"],
        required_fields=["page_text"],
    )

    results = make_agent().execute(task)

    assert "GST status is active." in results[0].field_value


def test_http_failure_evidence_handling():
    task = make_task(preferred_sources=["gst.gov.in"])
    agent = BrowserResearchAgent(fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("HTTP 500 error")))
    results = agent.execute(task)
    assert len(results) == 2
    assert results[0].confidence == 0.0
    assert results[1].confidence == 0.0
    assert results[1].field_value == "UNAVAILABLE"


def test_access_denied_evidence_handling():
    task = make_task(preferred_sources=["gst.gov.in"])
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><title>Access Denied</title><body>403 Forbidden cloudflare security check.</body></html>")
    results = agent.execute(task)
    assert len(results) == 2
    assert results[0].confidence == 0.0
    assert results[1].confidence == 0.0
    assert results[1].field_value == "UNAVAILABLE"


def test_empty_response_evidence_handling():
    task = make_task(preferred_sources=["gst.gov.in"])
    agent = BrowserResearchAgent(fetcher=lambda url: "   \n   ")
    results = agent.execute(task)
    assert len(results) == 2
    assert results[0].confidence == 0.0
    assert results[1].confidence == 0.0
    assert results[1].field_value == "UNAVAILABLE"


def test_irrelevant_page_evidence_handling():
    # Target is "29AAACI4798L1ZP", but page contains generic irrelevant content without target
    task = make_task(preferred_sources=["gst.gov.in"], target="29AAACI4798L1ZP")
    long_irrelevant_body = (
        "This page displays general tax statistics with no company listings. "
        "We have data on imports and exports of agricultural goods, industrial machinery, and consumer electronics. "
        "Tax collections have increased across all states by an average of five percent compared to the previous fiscal year. "
        "Please check back later for updated reports on regional tax divisions. "
        "This is generic filler text to make the page exceed one hundred words so that the relevance check is triggered. "
        "Filler text continue to ensure length requirement is met. "
        "More and more words are being added here to simulate a real website that has irrelevant content. "
        "Almost there, adding some more sentences about economic growth, financial markets, and global trade updates."
    )
    agent = BrowserResearchAgent(fetcher=lambda url: f"<html><title>Generic Portal</title><body>{long_irrelevant_body}</body></html>")
    results = agent.execute(task)
    assert len(results) == 2
    assert results[0].confidence == 0.0
    assert results[1].confidence == 0.0
    assert results[1].field_value == "UNAVAILABLE"


def test_valid_source_response_evidence_handling():
    # Target is "27ABCDE1234F1Z5", and page text contains target and name
    task = make_task(preferred_sources=["gst.gov.in"], target="27ABCDE1234F1Z5")
    html_content = "<html><title>GST Info</title><body>GSTIN: 27ABCDE1234F1Z5 is active. ABC Foods Private Limited.</body></html>"
    agent = BrowserResearchAgent(fetcher=lambda url: html_content)
    results = agent.execute(task)
    assert len(results) == 2
    assert results[0].confidence == 0.95
    assert results[0].field_value == "GST Info"
    assert results[1].field_value == "AVAILABLE"


def test_candidate_entities_empty_on_failure():
    task = make_task(
        task_type="ENTITY_DISCOVERY",
        target="abcfoods.in",
        preferred_sources=["generic_web"],
        required_fields=["candidate_entities"],
    )
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><title>Access Denied</title><body>CF Blocked page.</body></html>")
    results = agent.execute(task)
    assert len(results) == 1
    assert results[0].confidence == 0.0
    assert results[0].field_value == []


def test_report_risk_ignores_failed_evidence():
    from app.risk.engine import calculate_risk_analysis
    from app.graph.state import ResearchResult
    
    # 1. Create a failed research result (confidence 0.0)
    r1 = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="gst_status",
        field_value="Inactive",  # Inactive normally triggers GST_INACTIVE
        source_name="GST Portal",
        source_url="https://www.gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z",
        confidence=0.0,  # FAILED
    )
    
    # 2. Run risk engine analysis
    analysis = calculate_risk_analysis([r1])
    # It should NOT trigger the GST_INACTIVE rule because evidence confidence is < 0.5
    assert "GST_INACTIVE" not in analysis["risk_signals"]
    assert analysis["overall_risk"]["score"] == 0

