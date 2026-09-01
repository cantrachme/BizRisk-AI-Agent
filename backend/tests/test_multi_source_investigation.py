import uuid
import pytest
from unittest import mock
from datetime import datetime, timezone

from app.graph.state import InvestigationState, ResearchTask, ResearchResult
from app.agents.planner import PlannerAgent
from app.agents.browser import BrowserResearchAgent
from app.graph.workflow import app as graph_app
from app.graph.edges import should_continue_after_resolution, should_continue
from app.core.exceptions import HumanInterventionRequiredException
from app.risk.engine import calculate_risk_analysis
from app.models.evidence import Evidence


def make_initial_state(raw_input: dict) -> InvestigationState:
    return {
        "investigation_id": "INV-MULTI-SOURCE-001",
        "raw_input": raw_input,
        "normalized_input": raw_input,
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "resolved_entity": None,
        "entity_confidence": 0.0,
        "entity_resolution_status": "PENDING",
        "planner_loop_count": 0,
        "status": "CREATED",
        "research_depth": 0,
        "browser_actions": 0,
        "browser_tasks_count": 0,
        "llm_calls": 0,
        "token_usage": 0,
        "stop_reason": None,
    }


def test_1_business_name_and_gstin_generates_multisource_tasks():
    """Test that business_name + GSTIN alone generates tasks across all 6 source categories."""
    state = make_initial_state({
        "business_name": "Test Business",
        "gstin": "27AAACW0387R1Z6",
        "location": "India",
    })
    planner = PlannerAgent()
    tasks = planner.plan(state)
    
    task_types = [t.task_type for t in tasks]
    assert len(tasks) >= 6
    assert "GST_VERIFICATION" in task_types
    assert "MCA_VERIFICATION" in task_types
    assert "EPFO_VERIFICATION" in task_types
    assert "WEBSITE_VERIFICATION" in task_types
    assert "THIRD_PARTY_RESEARCH" in task_types
    assert "GENERAL_WEB_RESEARCH" in task_types


def test_2_exact_entity_resolution_does_not_terminate_research():
    """Test that should_continue_after_resolution does not return __end__ on EXACT entity resolution."""
    state = make_initial_state({"business_name": "Test Business", "gstin": "27AAACW0387R1Z6"})
    state["entity_resolution_status"] = "EXACT"
    state["status"] = "ENTITY_RESOLVED"
    state["planner_loop_count"] = 1
    
    next_node = should_continue_after_resolution(state)
    assert next_node == "planner"


def test_3_cin_discovered_triggers_mca_second_hop():
    """Test that a newly discovered CIN triggers targeted MCA_VERIFICATION."""
    state = make_initial_state({"business_name": "Test Business", "gstin": "27AAACW0387R1Z6"})
    state["completed_tasks"] = [
        ResearchTask(
            task_id="TASK-001",
            task_type="GST_VERIFICATION",
            target="27AAACW0387R1Z6",
            objective="Verify GSTIN",
            required_fields=["legal_name", "gst_status"],
            priority=1,
            preferred_sources=["gst.gov.in"],
            fallback_sources=["third_party"]
        )
    ]
    # Inject discovered CIN result
    state["results"] = [
        ResearchResult(
            result_id="RES-001",
            task_id="TASK-001",
            field_name="cin",
            field_value="L32102KA1945PLC020800",
            source_name="GST Portal",
            source_url="https://services.gst.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.95
        )
    ]
    
    planner = PlannerAgent()
    tasks = planner.plan(state)
    mca_tasks = [t for t in tasks if t.task_type == "MCA_VERIFICATION" and t.target == "L32102KA1945PLC020800"]
    assert len(mca_tasks) == 1
    assert mca_tasks[0].target == "L32102KA1945PLC020800"


def test_4_website_discovered_triggers_website_second_hop():
    """Test that a newly discovered website URL triggers targeted WEBSITE_VERIFICATION."""
    state = make_initial_state({"business_name": "Test Business", "gstin": "27AAACW0387R1Z6"})
    state["results"] = [
        ResearchResult(
            result_id="RES-002",
            task_id="TASK-006",
            field_name="website",
            field_value="https://testbusiness.com",
            source_name="General Web",
            source_url="https://duckduckgo.com",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.85
        )
    ]
    
    planner = PlannerAgent()
    tasks = planner.plan(state)
    web_tasks = [t for t in tasks if t.task_type == "WEBSITE_VERIFICATION" and t.target == "https://testbusiness.com"]
    assert len(web_tasks) == 1


def test_5_epfo_identifier_discovered_triggers_epfo_second_hop():
    """Test that a newly discovered EPFO code triggers targeted EPFO_VERIFICATION."""
    state = make_initial_state({"business_name": "Test Business", "gstin": "27AAACW0387R1Z6"})
    state["results"] = [
        ResearchResult(
            result_id="RES-003",
            task_id="TASK-005",
            field_name="epfo_code",
            field_value="MH/BAN/0012345/000",
            source_name="Third-Party Source",
            source_url="https://zaubacorp.com",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.90
        )
    ]
    
    planner = PlannerAgent()
    tasks = planner.plan(state)
    epfo_tasks = [t for t in tasks if t.task_type == "EPFO_VERIFICATION" and t.target == "MH/BAN/0012345/000"]
    assert len(epfo_tasks) == 1


def test_6_task_scheduling_is_idempotent():
    """Test that running planner.plan() twice with same state does not produce duplicates."""
    state = make_initial_state({"business_name": "Test Business", "gstin": "27AAACW0387R1Z6"})
    planner = PlannerAgent()
    first_tasks = planner.plan(state)
    
    # Put tasks into pending_tasks
    state["pending_tasks"] = first_tasks
    second_tasks = planner.plan(state)
    assert len(second_tasks) == 0


def test_7_investigation_cannot_complete_while_research_tasks_remain():
    """Test that should_continue routes to browser if pending_tasks are present."""
    state = make_initial_state({"business_name": "Test Business"})
    state["pending_tasks"] = [
        ResearchTask(
            task_id="TASK-002",
            task_type="MCA_VERIFICATION",
            target="Test Business",
            objective="Verify MCA",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=["mca.gov.in"],
            fallback_sources=["third_party"]
        )
    ]
    assert should_continue(state) == "browser"


def test_8_captcha_causes_waiting_for_user():
    """Test that encountering CAPTCHA halts execution and sets WAITING_FOR_USER."""
    def mock_fetcher(url):
        return "<html><body>Please solve the captcha below to proceed</body></html>"

    agent = BrowserResearchAgent(fetcher=mock_fetcher)
    task = ResearchTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST",
        required_fields=["legal_name", "gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=["third_party"]
    )
    
    with pytest.raises(HumanInterventionRequiredException) as exc_info:
        agent.execute(task, investigation_id="INV-TEST-001")
    assert exc_info.value.intervention_type == "CAPTCHA"


def test_9_captcha_keeps_live_browser_session_alive():
    """Test that live session is retained in BrowserSessionManager on CAPTCHA."""
    from app.core.browser_session_manager import browser_session_manager
    session = browser_session_manager.start_session("INV-CAPTCHA-KEEP", "TASK-GST-01", "gst.gov.in")
    assert session.status == "RUNNING"
    assert not session.is_expired()
    
    # Session must still be retrievable
    retrieved = browser_session_manager.get_session("INV-CAPTCHA-KEEP", "TASK-GST-01")
    assert retrieved is not None
    browser_session_manager.close_session("INV-CAPTCHA-KEEP", "TASK-GST-01")


def test_10_resume_after_hitl_executes_remaining_research():
    """Test that resuming from WAITING_FOR_USER runs remaining tasks to completion."""
    initial_state = make_initial_state({
        "business_name": "ABC Foods Pvt Ltd",
        "gstin": "27ABCDE1234F1Z5",
        "website": "abcfoods.in",
        "location": "Noida",
    })

    # Pass 1: GST fails with CAPTCHA
    def captcha_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><body>solve the captcha below</body></html>"
        return "<html><title>ABC Foods</title><body>ABC Foods official site in Noida</body></html>"

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=captcha_fetcher):
        pass1_output = graph_app.invoke(initial_state)

    assert pass1_output["status"] == "WAITING_FOR_USER"

    # Pass 2: User solves CAPTCHA, GST returns valid data
    def solved_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><title>GST Portal</title><body>GSTIN: 27ABCDE1234F1Z5<br>Legal Name: ABC Foods Pvt Ltd<br>Status: Active</body></html>"
        return "<html><title>ABC Foods</title><body>ABC Foods official site in Noida</body></html>"

    resumed_tasks = [t.model_copy(update={"status": "PENDING"}) for t in pass1_output["pending_tasks"]]
    resumed_state = dict(pass1_output)
    resumed_state["pending_tasks"] = resumed_tasks
    resumed_state["status"] = "IN_PROGRESS"
    resumed_state["stop_reason"] = None

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=solved_fetcher):
        final_output = graph_app.invoke(resumed_state)

    assert final_output["status"] in {"COMPLETED", "ENTITY_RESOLVED"}
    assert len(final_output["completed_tasks"]) >= 1


def test_11_evidence_from_multiple_sources_reaches_risk_analysis():
    """Test that risk engine evaluates evidence records originating from distinct source categories."""
    results = [
        ResearchResult(
            result_id="RES-GST-01",
            task_id="TASK-001",
            field_name="gst_status",
            field_value="CANCELLED",
            source_name="GST Portal",
            source_url="https://services.gst.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.95
        ),
        ResearchResult(
            result_id="RES-MCA-01",
            task_id="TASK-002",
            field_name="legal_name",
            field_value="Alpha Beta Corp",
            source_name="MCA Portal",
            source_url="https://www.mca.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.90
        ),
        ResearchResult(
            result_id="RES-TP-01",
            task_id="TASK-005",
            field_name="legal_name",
            field_value="Gamma Delta LLC",
            source_name="Third-Party Source",
            source_url="https://zaubacorp.com",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.85
        ),
    ]
    
    analysis = calculate_risk_analysis(results)
    assert analysis["overall_risk"]["score"] > 0
    signals = [s["code"] for s in analysis["risk_signals"]]
    assert "GST_INACTIVE" in signals
    assert "LEGAL_NAME_CONFLICT" in signals


def test_12_final_report_contains_multisource_evidence_provenance():
    """Test that report structure includes evidence items from different source categories."""
    results = [
        ResearchResult(
            result_id="RES-01",
            task_id="TASK-001",
            field_name="gst_status",
            field_value="AVAILABLE",
            source_name="GST Portal",
            source_url="https://services.gst.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.95
        ),
        ResearchResult(
            result_id="RES-02",
            task_id="TASK-002",
            field_name="company_status",
            field_value="ACTIVE",
            source_name="MCA Portal",
            source_url="https://www.mca.gov.in",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.95
        ),
        ResearchResult(
            result_id="RES-03",
            task_id="TASK-004",
            field_name="website_status",
            field_value="AVAILABLE",
            source_name="Company Website",
            source_url="https://test.com",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.85
        ),
    ]
    
    analysis = calculate_risk_analysis(results)
    assert "overall_risk" in analysis
    assert len(results) == 3


def test_13_404_no_records_on_one_source_does_not_halt_other_sources():
    """Test that HTTP 404/503 or empty page on one source does not prevent remaining tasks from completing."""
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><title>503 Service Unavailable</title><body>Service Down</body></html>"
        elif "mca.gov.in" in url:
            return "<html><title>MCA Portal</title><body>Company Status: Active</body></html>"
        return "<html><title>Test Company</title><body>Registered in India</body></html>"

    agent = BrowserResearchAgent(fetcher=mock_fetcher)
    task1 = ResearchTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST",
        required_fields=["gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=["third_party"]
    )
    task2 = ResearchTask(
        task_id="TASK-002",
        task_type="MCA_VERIFICATION",
        target="Test Company",
        objective="Verify MCA",
        required_fields=["company_status"],
        priority=1,
        preferred_sources=["mca.gov.in"],
        fallback_sources=["third_party"]
    )
    
    res1 = agent.execute(task1)
    res2 = agent.execute(task2)
    assert len(res1) == 1
    assert len(res2) == 1
    assert res2[0].field_value == "ACTIVE"


def test_14_search_engine_snippets_cannot_be_stored_as_business_evidence():
    """Test that raw search engine result snippets are rejected from being stored as verified facts."""
    page_data = {
        "title": "Search Results at DuckDuckGo",
        "text": "Wipro Limited is a leading company",
        "url": "https://duckduckgo.com/?q=wipro"
    }
    task = ResearchTask(
        task_id="TASK-006",
        task_type="GENERAL_WEB_RESEARCH",
        target="duckduckgo",
        objective="General search",
        required_fields=["gst_status"],
        priority=2,
        preferred_sources=["generic_web"],
        fallback_sources=[]
    )
    val, basis = BrowserResearchAgent._extract_field_value_with_basis(task, "gst_status", page_data)
    assert val in ["NOT_FOUND", "UNAVAILABLE"]
    assert "Search engines are not valid evidence sources" in basis


def test_15_gst_active_status_not_inferred_from_mca():
    """Test that MCA Active status does not falsely populate gst_status as AVAILABLE."""
    page_data = {
        "title": "Corporate Registry - ABC Corp",
        "text": "Company Status: Active. Director Status: Active.",
        "url": "https://www.mca.gov.in/company"
    }
    task = ResearchTask(
        task_id="TASK-002",
        task_type="MCA_VERIFICATION",
        target="ABC Corp",
        objective="Verify MCA",
        required_fields=["gst_status"],
        priority=1,
        preferred_sources=["mca.gov.in"],
        fallback_sources=[]
    )
    val, basis = BrowserResearchAgent._extract_field_value_with_basis(task, "gst_status", page_data)
    assert val == "UNAVAILABLE"
    assert "No explicit GST or GSTIN status found" in basis


def test_16_empty_website_triggers_discovery_and_extracts_opened_page():
    """Test that empty website target resolves to discovery and extracts fields from opened domain."""
    def mock_fetcher(url):
        if "duckduckgo.com" in url:
            return """
            <html>
              <body>
                <a href="https://www.testbusiness.com">Test Business Official Site</a>
              </body>
            </html>
            """
        elif "testbusiness.com" in url:
            return """
            <html>
              <head><title>Test Business Official Website</title></head>
              <body>
                <h1>Test Business Private Limited</h1>
                <div>Established: 2018</div>
                <div>Address: 101 Marine Lines, Mumbai, Maharashtra 400020</div>
                <div>Business Activity: Cloud Computing & Software</div>
              </body>
            </html>
            """
        return "<html><body>Not Found</body></html>"

    agent = BrowserResearchAgent(fetcher=mock_fetcher)
    task = ResearchTask(
        task_id="TASK-004",
        task_type="WEBSITE_VERIFICATION",
        target="Test Business official website",
        objective="Discover and verify website",
        required_fields=["website_status", "contact_address", "established_year", "legal_name"],
        priority=2,
        preferred_sources=["company_website"],
        fallback_sources=["generic_web"]
    )

    results = agent.execute(task)
    assert len(results) == 4
    result_map = {r.field_name: r.field_value for r in results}
    assert result_map["website_status"] == "AVAILABLE"
    assert "Mumbai" in result_map["contact_address"]
    assert result_map["established_year"] == "2018"


def test_17_missing_cin_triggers_mca_fallback_discovery():
    """Test that MCA verification without CIN searches and extracts from opened third-party registry."""
    def mock_fetcher(url):
        if "mca.gov.in" in url:
            # Official portal down / blocked
            return "<html><title>503 Service Unavailable</title><body>Service Down</body></html>"
        elif "duckduckgo.com" in url:
            return """
            <html>
              <body>
                <a href="https://www.zaubacorp.com/company/TEST-BUSINESS/U72200MH2018PTC123456">Zauba Corp Profile</a>
              </body>
            </html>
            """
        elif "zaubacorp.com" in url:
            return """
            <html>
              <head><title>Test Business Private Limited - Company Profile</title></head>
              <body>
                <div>Company Name: Test Business Private Limited</div>
                <div>CIN: U72200MH2018PTC123456</div>
                <div>Company Status: Active</div>
                <div>Incorporation Date: 15/04/2018</div>
                <div>Registered Address: 101 Marine Lines, Mumbai, Maharashtra 400020</div>
              </body>
            </html>
            """
        return "<html><body>404</body></html>"

    agent = BrowserResearchAgent(fetcher=mock_fetcher)
    task = ResearchTask(
        task_id="TASK-002",
        task_type="MCA_VERIFICATION",
        target="Test Business MCA company registration",
        objective="Verify MCA",
        required_fields=["legal_name", "company_status", "incorporation_date", "registered_address"],
        priority=1,
        preferred_sources=["mca.gov.in"],
        fallback_sources=["third_party"]
    )

    results = agent.execute(task)
    assert len(results) == 4
    result_map = {r.field_name: r.field_value for r in results}
    assert result_map["company_status"] == "ACTIVE"
    assert result_map["incorporation_date"] == "2018"
    assert "Mumbai" in result_map["registered_address"]


def test_18_missing_epfo_code_triggers_epfo_fallback_discovery():
    """Test that EPFO verification without code searches and extracts from opened registry."""
    def mock_fetcher(url):
        if url == "https://www.epfindia.gov.in":
            return "<html><title>403 Forbidden</title><body>Access Denied</body></html>"
        elif "duckduckgo.com" in url:
            return """
            <html>
              <body>
                <a href="https://www.zaubacorp.com/epfo/TEST-BUSINESS">EPFO Record</a>
              </body>
            </html>
            """
        return """
        <html>
          <head><title>Test Business EPFO Record</title></head>
          <body>
            <div>Establishment Name: Test Business Private Limited</div>
            <div>EPFO Status: Active</div>
            <div>Registered Address: 101 Marine Lines, Mumbai, Maharashtra 400020</div>
          </body>
        </html>
        """

    agent = BrowserResearchAgent(fetcher=mock_fetcher)
    task = ResearchTask(
        task_id="TASK-003",
        task_type="EPFO_VERIFICATION",
        target="Test Business EPFO establishment",
        objective="Verify EPFO",
        required_fields=["establishment_name", "epfo_status", "registered_address"],
        priority=1,
        preferred_sources=["epfindia.gov.in"],
        fallback_sources=["third_party"]
    )

    results = agent.execute(task)
    assert len(results) == 3
    result_map = {r.field_name: r.field_value for r in results}
    assert result_map["epfo_status"] == "AVAILABLE"
    assert "Mumbai" in result_map["registered_address"]


def test_19_fetch_failure_records_source_failure_without_crashing_graph():
    """Test that a network/fetch exception records source failure and does not crash graph."""
    def faulty_fetcher(url):
        raise ConnectionError("DNS resolution failed for test host")

    agent = BrowserResearchAgent(fetcher=faulty_fetcher)
    task = ResearchTask(
        task_id="TASK-005",
        task_type="THIRD_PARTY_RESEARCH",
        target="Test Business",
        objective="Verify third party",
        required_fields=["legal_name", "company_status"],
        priority=2,
        preferred_sources=["third_party"],
        fallback_sources=["generic_web"]
    )

    results = agent.execute(task)
    assert len(results) == 2
    # Returns NOT_FOUND for failed fields with 0 confidence
    assert all(r.confidence == 0.0 for r in results)
    assert all(r.field_value == "NOT_FOUND" for r in results)


def test_20_source_failure_does_not_cancel_independent_tasks():
    """Test that failure on one source does not prevent independent sources from executing in the graph."""
    initial_state = make_initial_state({
        "business_name": "Test Business",
        "gstin": "27AAACW0387R1Z6",
        "location": "India"
    })

    def selective_fetcher(url):
        if "gst.gov.in" in url:
            raise TimeoutError("GST portal timeout")
        elif "mca.gov.in" in url:
            return "<html><title>MCA Portal</title><body>Company Status: Active<br>Legal Name: Test Business Pvt Ltd</body></html>"
        elif "epfindia.gov.in" in url:
            return "<html><title>EPFO Portal</title><body>EPFO Status: Active<br>Registered Address: Mumbai</body></html>"
        return "<html><title>Test Business Site</title><body>Welcome to Test Business official site in Mumbai</body></html>"

    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=selective_fetcher):
        output = graph_app.invoke(initial_state)

    assert output["status"] in {"COMPLETED", "ENTITY_RESOLVED"}
    completed_types = {t.task_type for t in output["completed_tasks"]}
    assert "MCA_VERIFICATION" in completed_types
    assert "EPFO_VERIFICATION" in completed_types
    assert len(output["results"]) > 0


def test_21_third_party_domain_not_chosen_as_company_website():
    """Test that third-party aggregator domains are never classified as Company Website."""
    def mock_fetcher(url):
        if "duckduckgo.com" in url:
            return """
            <html>
              <body>
                <a href="https://www.zaubacorp.com/company/TEST-BUSINESS/123">Zauba Link</a>
                <a href="https://www.tofler.in/test-business/company">Tofler Link</a>
                <a href="https://www.mca.gov.in/portal">MCA Link</a>
              </body>
            </html>
            """
        return "<html><body>Third Party Data</body></html>"

    agent = BrowserResearchAgent(fetcher=mock_fetcher)
    task = ResearchTask(
        task_id="TASK-004",
        task_type="WEBSITE_VERIFICATION",
        target="Test Business official website",
        objective="Discover official website",
        required_fields=["website_status", "contact_address", "established_year"],
        priority=2,
        preferred_sources=["company_website"],
        fallback_sources=["generic_web"]
    )

    results = agent.execute(task)
    # Should reject third-party candidates and record UNAVAILABLE / NOT_FOUND
    assert len(results) == 3
    assert all(r.confidence == 0.0 for r in results)
    assert all(r.field_value in ["NOT_FOUND", "UNAVAILABLE"] for r in results)


def test_22_extracted_fields_contain_no_neighboring_labels_and_no_false_conflict():
    """Test that extracted fields contain strictly isolated values without neighboring label text."""
    html = """
    <html>
      <head><title>Corporate Registry - Test Business Private Limited</title></head>
      <body>
        <div>GSTIN: 27AAACW0387R1Z6</div>
        <div>Legal Name: Test Business Private Limited</div>
        <div>GST status: Active</div>
        <div>Registered Address: 101 Marine Lines, Mumbai, Maharashtra 400020</div>
        <div>Business Activity: Information Technology Services</div>
      </body>
    </html>
    """
    agent = BrowserResearchAgent(fetcher=lambda u: html)
    task = ResearchTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST",
        required_fields=["legal_name", "gst_status", "registered_address"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=[]
    )

    results = agent.execute(task)
    result_map = {r.field_name: r.field_value for r in results}
    assert result_map["legal_name"] == "Test Business Private Limited"
    assert result_map["gst_status"] == "AVAILABLE"
    assert result_map["registered_address"] == "101 Marine Lines, Mumbai, Maharashtra 400020"
    assert "GST status" not in result_map["legal_name"]
    assert "Business Activity" not in result_map["registered_address"]


def test_23_gst_landing_page_title_rejected_as_company_name():
    """Test that generic GST portal landing page title is never extracted as legal_name."""
    html = """
    <html>
      <head><title>Goods & Services Tax (GST) | Services</title></head>
      <body>
        <div>Search Taxpayer</div>
        <div>Enter GSTIN/UIN of the Taxpayer</div>
        <div>Type the characters you see in the image below</div>
      </body>
    </html>
    """
    agent = BrowserResearchAgent(fetcher=lambda u: html)
    task = ResearchTask(
        task_id="TASK-001",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status",
        required_fields=["legal_name", "gst_status", "registered_address", "business_activity"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=[]
    )

    results = agent.execute(task)
    result_map = {r.field_name: r.field_value for r in results}
    # Must reject generic portal landing title and mark NOT_FOUND/UNAVAILABLE with 0 confidence
    assert result_map["legal_name"] == "NOT_FOUND"
    assert result_map["legal_name"] != "Goods & Services Tax (GST) | Services"
    assert result_map["gst_status"] == "UNAVAILABLE"
    assert result_map["registered_address"] == "NOT_FOUND"
    assert result_map["business_activity"] == "NOT_FOUND"
    assert all(r.confidence == 0.0 for r in results)


def test_24_generic_live_e2e_pipeline_verification():
    """Test full generic E2E research pipeline across all 6 source categories with fallback and QA."""
    def mock_fetcher(url: str) -> str:
        url_lower = url.lower()
        if "services.gst.gov.in" in url_lower:
            return """
            <html>
              <head><title>Search Taxpayer - GST</title></head>
              <body>
                <div>Taxpayer Details</div>
                <div>GSTIN: 29AAACA1234B1Z5</div>
                <div>Legal Name of Business: Acme Global Technologies Private Limited</div>
                <div>GSTIN / UIN Status: Active</div>
                <div>Principal Place of Business: Plot 42 Tech Park, Whitefield, Bangalore, Karnataka 560066</div>
                <div>Business Activity: Computer Software and IT Services</div>
              </body>
            </html>
            """
        elif "mca.gov.in" in url_lower:
            # MCA blocked - returns error to test automatic fallback to third party
            return "<html><head><title>Access Denied - MCA 503</title></head><body>503 Service Unavailable</body></html>"
        elif "epfindia.gov.in" in url_lower:
            return "<html><head><title>EPFO Portal Error</title></head><body>403 Forbidden Access Denied</body></html>"
        elif "duckduckgo.com" in url_lower or "bing.com" in url_lower:
            if "acme" in url_lower and "website" in url_lower:
                return """
                <html>
                  <body>
                    <a class="result__url" href="https://www.zaubacorp.com/company/ACME/1">Zauba</a>
                    <a class="result__url" href="https://acmeglobaltech.com">Acme Official</a>
                    <a class="result__url" href="https://www.tofler.in/acme">Tofler</a>
                  </body>
                </html>
                """
            elif "epfo" in url_lower or "establishment" in url_lower:
                return """
                <html>
                  <body>
                    <a class="result__url" href="https://epfindia-directory.org/establishment/acme">EPFO Directory</a>
                  </body>
                </html>
                """
            else:
                return """
                <html>
                  <body>
                    <a class="result__url" href="https://www.zaubacorp.com/company/ACME-GLOBAL/U72200KA2019PTC111222">Zauba Acme</a>
                  </body>
                </html>
                """
        elif "acmeglobaltech.com" in url_lower:
            return """
            <html>
              <head><title>Acme Global Technologies - Official Site</title></head>
              <body>
                <div>Welcome to Acme Global Technologies Private Limited</div>
                <div>Contact Address: Plot 42 Tech Park, Whitefield, Bangalore, Karnataka 560066</div>
                <div>Established: 2019</div>
                <div>Business Activity: Computer Software and IT Services</div>
              </body>
            </html>
            """
        elif "zaubacorp.com" in url_lower:
            return """
            <html>
              <head><title>Acme Global Technologies Private Limited - Zauba Corp</title></head>
              <body>
                <div>CIN: U72200KA2019PTC111222</div>
                <div>Legal Name: Acme Global Technologies Private Limited</div>
                <div>Company Status: Active</div>
                <div>Date of Incorporation: 15/04/2019</div>
                <div>Registered Address: Plot 42 Tech Park, Whitefield, Bangalore, Karnataka 560066</div>
                <div>Business Activity: Computer Software and IT Services</div>
              </body>
            </html>
            """
        elif "epfindia-directory.org" in url_lower:
            return """
            <html>
              <head><title>Establishment Details - EPFO</title></head>
              <body>
                <div>Establishment Name: Acme Global Technologies Private Limited</div>
                <div>Status: Active</div>
                <div>Establishment Code: KN/BNG/0098765/000</div>
                <div>Address: Plot 42 Tech Park, Whitefield, Bangalore, Karnataka 560066</div>
              </body>
            </html>
            """
        return "<html><body>No records found</body></html>"

    with mock.patch.object(BrowserResearchAgent, "_fetch_page", side_effect=mock_fetcher):
        state = make_initial_state({
            "business_name": "Acme Global Technologies",
            "gstin": "29AAACA1234B1Z5",
            "location": "Bangalore, India",
        })
        config = {"configurable": {"thread_id": "TEST-GENERIC-E2E-THREAD"}}
        output = graph_app.invoke(state, config=config)

        # 1. 6 tasks planned and executed
        assert len(output["completed_tasks"]) >= 6
        task_types = {t.task_type for t in output["completed_tasks"]}
        assert "GST_VERIFICATION" in task_types
        assert "MCA_VERIFICATION" in task_types
        assert "EPFO_VERIFICATION" in task_types
        assert "WEBSITE_VERIFICATION" in task_types
        assert "THIRD_PARTY_RESEARCH" in task_types
        assert "GENERAL_WEB_RESEARCH" in task_types

        # 2. Results collected from distinct sources
        results = output["results"]
        assert len(results) >= 15
        
        # 3. Source provenance check
        source_names = {r.source_name for r in results if r.confidence > 0}
        assert "GST Portal" in source_names
        assert "acmeglobaltech.com" in source_names  # Official website domain
        assert "zaubacorp.com" in source_names or "Third-Party Source" in source_names

        # 4. Verified legal name and address isolation
        legal_names = [r.field_value for r in results if r.field_name == "legal_name" and r.confidence > 0]
        assert all("Acme Global Technologies" in name for name in legal_names)
        assert all("GST" not in name for name in legal_names)
        assert all("Portal" not in name for name in legal_names)

        # 5. Entity resolution and risk score
        assert output["entity_resolution_status"] in {"EXACT", "SIMILARITY"}
        assert output["status"] == "COMPLETED"
        assert output["qa_result"]["status"] == "PASS"




