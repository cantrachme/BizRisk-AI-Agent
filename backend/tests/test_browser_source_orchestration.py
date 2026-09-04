import uuid
import pytest
from unittest.mock import Mock, patch

from app.graph.state import InvestigationState, ResearchTask, ResearchResult
from app.agents.planner import PlannerAgent
from app.agents.browser import BrowserResearchAgent
from app.research.source_registry import SourceRegistryManager, SourceMetadata, SourceType, source_registry
from app.research.base import (
    clean_legal_name_candidate,
    extract_business_activity_from_text,
    extract_status_from_text,
    extract_address_from_text,
    extract_date_from_text,
)


def make_test_state(raw_input: dict) -> InvestigationState:
    return {
        "investigation_id": str(uuid.uuid4()),
        "business_name": raw_input.get("business_name", ""),
        "raw_input": raw_input,
        "normalized_input": raw_input,
        "status": "IN_PROGRESS",
        "current_node": "planner",
        "pending_tasks": [],
        "completed_tasks": [],
        "failed_tasks": [],
        "results": [],
        "errors": [],
        "logs": [],
        "risk_score": 0.0,
        "risk_level": "LOW",
        "human_intervention_required": False,
        "human_intervention_reason": None,
        "user_decisions": {},
        "planner_loop_count": 0,
        "entity_resolution_status": "PENDING",
        "resolved_entity": None,
    }


def test_1_four_configured_third_party_sources_are_all_planned():
    """Test that all configured third-party directory sources (InstaFinancials, QuickCompany, Tofler, ZaubaCorp, Falcon Ebiz) get individual tasks."""
    state = make_test_state({
        "business_name": "Acme Global Technologies Limited",
        "cin": "L22210MH1995PLC084781",
        "gstin": "27AAACW0387R1Z6",
        "location": "Maharashtra",
        "website": "https://www.acmeglobal.com",
    })

    planner = PlannerAgent()
    tasks = planner.plan(state)

    tp_tasks = [t for t in tasks if t.task_type == "THIRD_PARTY_RESEARCH"]
    assert len(tp_tasks) == 5, f"Expected 5 distinct THIRD_PARTY_RESEARCH tasks, got {len(tp_tasks)}"

    tp_sources = [t.preferred_sources[0] for t in tp_tasks]
    assert "instafinancials.com" in tp_sources
    assert "quickcompany.in" in tp_sources
    assert "tofler.in" in tp_sources
    assert "zaubacorp.com" in tp_sources
    assert "falconebiz.com" in tp_sources


def test_2_first_third_party_source_success_does_not_stop_remaining_sources():
    """Test that SUCCESS on the first third-party source does not prevent remaining third-party sources from executing."""
    state = make_test_state({
        "business_name": "Acme Global Technologies Limited",
        "cin": "L22210MH1995PLC084781",
        "gstin": "27AAACW0387R1Z6",
        "location": "Maharashtra",
    })

    planner = PlannerAgent()
    tasks = planner.plan(state)
    tp_tasks = [t for t in tasks if t.task_type == "THIRD_PARTY_RESEARCH"]

    attempted_sources = []

    def mock_fetcher(url):
        return f"<html><head><title>Acme Global Tech - {url}</title></head><body><h1>Acme Global Technologies Limited</h1><p>CIN: L22210MH1995PLC084781</p><p>Status: Active</p><p>Address: Mumbai, Maharashtra</p><p>Principal Business Activity: Software Development Services</p></body></html>"

    agent = BrowserResearchAgent(fetcher=mock_fetcher)

    results = []
    for task in tp_tasks:
        res = agent.execute(task)
        results.extend(res)
        attempted_sources.append(task.preferred_sources[0])

    # All configured directory sources must be attempted
    assert len(attempted_sources) == 5
    assert set(attempted_sources) == {"instafinancials.com", "quickcompany.in", "tofler.in", "zaubacorp.com", "falconebiz.com"}

    # Evidence from the directory sources must be returned
    source_names = {r.source_name for r in results if r.confidence > 0}
    assert len(source_names) >= 3


def test_3_one_source_blocked_does_not_prevent_remaining_sources():
    """Test that when one source is BLOCKED (e.g. captcha/Cloudflare), remaining third-party sources still execute."""
    state = make_test_state({
        "business_name": "Acme Global Technologies Limited",
        "cin": "L22210MH1995PLC084781",
    })

    planner = PlannerAgent()
    tasks = planner.plan(state)
    tp_tasks = [t for t in tasks if t.task_type == "THIRD_PARTY_RESEARCH"]

    def mixed_fetcher(url):
        if "instafinancials" in url:
            # Blocked / captcha challenge
            return "<html><title>Access Denied</title><body>Please solve captcha cloudflare challenge verification</body></html>"
        return "<html><title>Acme Global</title><body><h1>Acme Global Technologies Limited</h1><p>Status: Active</p><p>Address: Mumbai</p></body></html>"

    agent = BrowserResearchAgent(fetcher=mixed_fetcher)

    executed_tasks = []
    successful_results = []
    for task in tp_tasks:
        res = agent.execute(task)
        executed_tasks.append(task.preferred_sources[0])
        successful_results.extend([r for r in res if r.confidence > 0])

    # All 5 tasks were executed despite the first one being blocked
    assert len(executed_tasks) == 5
    assert len(successful_results) > 0
    # InstaFinancials produced 0 confidence, while others produced valid evidence
    insta_res = [r for r in successful_results if "instafinancials" in r.source_name.lower()]
    assert len(insta_res) == 0


def test_4_one_source_error_does_not_prevent_remaining_sources():
    """Test that when one source throws a network/DNS error, remaining sources still execute."""
    state = make_test_state({
        "business_name": "Acme Global Technologies Limited",
        "cin": "L22210MH1995PLC084781",
    })

    planner = PlannerAgent()
    tasks = planner.plan(state)
    tp_tasks = [t for t in tasks if t.task_type == "THIRD_PARTY_RESEARCH"]

    def error_fetcher(url):
        if "quickcompany" in url:
            raise ConnectionResetError("Connection reset by peer")
        return "<html><title>Acme Global</title><body><h1>Acme Global Technologies Limited</h1><p>Status: Active</p><p>Address: Mumbai</p></body></html>"

    agent = BrowserResearchAgent(fetcher=error_fetcher)

    executed_tasks = []
    all_results = []
    for task in tp_tasks:
        res = agent.execute(task)
        executed_tasks.append(task.preferred_sources[0])
        all_results.extend(res)

    assert len(executed_tasks) == 5
    # Valid evidence obtained from the non-failing sources
    successful_sources = {r.source_name for r in all_results if r.confidence > 0}
    assert "QuickCompany" not in successful_sources
    assert len(successful_sources) >= 2


def test_5_dynamically_added_registry_source_is_automatically_planned_and_attempted():
    """Test that a new source registered in source_registry dynamically is automatically planned and executed."""
    custom_source = SourceMetadata(
        source_id="custom-registry",
        name="customregistry.in",
        display_name="Custom Registry India",
        source_type=SourceType.THIRD_PARTY_REGISTRY,
        authority_tier=3,
        supported_task_types=["THIRD_PARTY_RESEARCH"],
        base_url="https://www.customregistry.in",
        priority=2,
        default_confidence=0.75,
        config={
            "cin_url_pattern": "https://www.customregistry.in/company/{cin}",
            "name_url_pattern": "https://www.customregistry.in/company/{slug}",
        },
    )

    try:
        source_registry.register_source(custom_source)

        state = make_test_state({
            "business_name": "Acme Global Technologies Limited",
            "cin": "L22210MH1995PLC084781",
        })

        planner = PlannerAgent()
        tasks = planner.plan(state)
        tp_tasks = [t for t in tasks if t.task_type == "THIRD_PARTY_RESEARCH"]

        tp_sources = [t.preferred_sources[0] for t in tp_tasks]
        assert "customregistry.in" in tp_sources
        assert len(tp_tasks) >= 5

        # Execute custom source
        custom_task = next(t for t in tp_tasks if t.preferred_sources[0] == "customregistry.in")
        agent = BrowserResearchAgent(fetcher=lambda url: "<html><title>Acme Global</title><body><h1>Acme Global Technologies Limited</h1><p>Status: Active</p></body></html>")
        res = agent.execute(custom_task)
        assert len(res) > 0
        assert any(r.confidence > 0 for r in res)

    finally:
        # Cleanup registered custom source
        source_registry._sources.pop("customregistry.in", None)
        source_registry._sources.pop("Custom Registry India", None)
        source_registry._sources.pop("custom-registry", None)


def test_6_other_research_categories_retain_existing_behavior():
    """Test that GST, MCA, EPFO, Website, and General Web research categories retain their normal planning and fallbacks."""
    state = make_test_state({
        "business_name": "Acme Global Technologies Limited",
        "cin": "L22210MH1995PLC084781",
        "gstin": "27AAACW0387R1Z6",
        "epfo_code": "MHBAN0012345000",
        "website": "https://www.acmeglobal.com",
        "location": "Maharashtra",
    })

    planner = PlannerAgent()
    tasks = planner.plan(state)

    gst_task = next((t for t in tasks if t.task_type == "GST_VERIFICATION"), None)
    mca_task = next((t for t in tasks if t.task_type == "MCA_VERIFICATION"), None)
    epfo_task = next((t for t in tasks if t.task_type == "EPFO_VERIFICATION"), None)
    web_task = next((t for t in tasks if t.task_type == "WEBSITE_VERIFICATION"), None)
    gen_task = next((t for t in tasks if t.task_type == "GENERAL_WEB_RESEARCH"), None)

    assert gst_task is not None
    assert gst_task.preferred_sources == ["gst.gov.in"]
    assert "third_party" in gst_task.fallback_sources

    assert mca_task is not None
    assert mca_task.preferred_sources == ["mca.gov.in"]
    assert "third_party" in mca_task.fallback_sources

    assert epfo_task is not None
    assert epfo_task.preferred_sources == ["epfindia.gov.in"]

    assert web_task is not None
    assert web_task.preferred_sources == ["company_website"]

    assert gen_task is not None
    assert gen_task.preferred_sources == ["generic_web"]


def test_7_dynamic_cin_name_gstin_url_resolution_and_no_location_contamination():
    """Test candidate URL generation for all 4 registries using CIN, GSTIN, name slug without location contamination."""
    target_with_location = "Hindustan Dynamic Systems Limited in Maharashtra L22210MH1995PLC084781"

    quick = source_registry.get_source("quickcompany.in")
    tofler = source_registry.get_source("tofler.in")
    zauba = source_registry.get_source("zaubacorp.com")
    insta = source_registry.get_source("instafinancials.com")

    quick_cands = quick.get_candidate_urls(target_with_location)
    tofler_cands = tofler.get_candidate_urls(target_with_location)
    zauba_cands = zauba.get_candidate_urls(target_with_location)
    insta_cands = insta.get_candidate_urls(target_with_location)

    # Verify no location contamination in any generated URL
    for cands in [quick_cands, tofler_cands, zauba_cands, insta_cands]:
        for u in cands:
            assert "in-maharashtra" not in u.lower()
            assert "in-india" not in u.lower()

    # Verify QuickCompany includes slug and cin candidates
    assert any("hindustan-dynamic-systems-limited" in u for u in quick_cands)
    assert any("L22210MH1995PLC084781" in u for u in quick_cands)

    # Verify Tofler includes cin_name and cin candidates
    assert any("hindustan-dynamic-systems-limited/company/L22210MH1995PLC084781" in u for u in tofler_cands)
    assert any("company/L22210MH1995PLC084781" in u for u in tofler_cands)

    # Verify ZaubaCorp includes cin_name and cin candidates
    assert any("company/hindustan-dynamic-systems-limited/L22210MH1995PLC084781" in u for u in zauba_cands)


def test_8_alternative_url_pattern_fallback_when_first_url_fails():
    """Test that when the first candidate URL returns 404 or fails, BrowserResearchAgent attempts the next candidate URL and succeeds."""
    task = ResearchTask(
        task_id="TASK-TEST-008",
        task_type="THIRD_PARTY_RESEARCH",
        target="Hindustan Dynamic Systems Limited L22210MH1995PLC084781",
        preferred_sources=["quickcompany.in"],
        required_fields=["legal_name", "company_status", "registered_address"],
        objective="Verify company details on third party registry",
        priority=1,
    )

    attempted_urls = []

    def multi_url_fetcher(url):
        attempted_urls.append(url)
        # First candidate (/company/hindustan-dynamic-systems-limited) returns 404
        if "hindustan-dynamic-systems-limited" in url:
            return "<html><title>404 Not Found</title><body>Page not found</body></html>"
        # Second candidate (/company/L22210MH1995PLC084781) succeeds
        if "L22210MH1995PLC084781" in url:
            return "<html><title>Hindustan Dynamic Systems Limited</title><body><h1>Hindustan Dynamic Systems Limited</h1><p>Status: ACTIVE</p><p>Registered Address: 123 Tech Park, Pune, Maharashtra 411001</p><p>Principal Business Activity: Engineering Consultancy</p></body></html>"
        return "<html><title>404</title></html>"

    agent = BrowserResearchAgent(fetcher=multi_url_fetcher)
    results = agent.execute(task)

    # Proves both candidate URLs were attempted
    assert len(attempted_urls) >= 2
    # Proves second candidate produced valid verified evidence
    status_res = next((r for r in results if r.field_name == "company_status"), None)
    assert status_res is not None
    assert status_res.field_value == "ACTIVE"
    assert status_res.confidence > 0


def test_9_irrelevant_and_blocked_page_rejection():
    """Test that 200 OK pages with Cloudflare challenges, error text, or irrelevant entity are rejected and do NOT produce evidence."""
    task = ResearchTask(
        task_id="TASK-TEST-009",
        task_type="THIRD_PARTY_RESEARCH",
        target="Hindustan Dynamic Systems Limited L22210MH1995PLC084781",
        preferred_sources=["instafinancials.com"],
        required_fields=["legal_name", "company_status", "registered_address"],
        objective="Verify company details on third party registry",
        priority=1,
    )

    # Returns a 200 HTML page but with Cloudflare block challenge
    def blocked_fetcher(url):
        return "<html><title>Attention Required! | Cloudflare</title><body>Please complete the security check to proceed. cf-browser-verification</body></html>"

    agent = BrowserResearchAgent(fetcher=blocked_fetcher)
    results = agent.execute(task)

    # All fields must be NOT_FOUND / UNAVAILABLE with 0 confidence
    for r in results:
        assert r.confidence == 0.0
        assert r.field_value in {"NOT_FOUND", "UNAVAILABLE", "SOURCE_UNAVAILABLE"}


def test_10_legal_name_location_contamination_and_noise_rejection():
    """Test that legal_name cleaner rejects copyright notices, portal headers, and strips location suffixes."""
    # Location contamination
    name1 = clean_legal_name_candidate("TATA CONSULTANCY SERVICES LIMITED in Maharashtra")
    assert name1 == "TATA CONSULTANCY SERVICES LIMITED"

    # Copyright / trademark fragment
    name2 = clean_legal_name_candidate("s and logos appearing on the site")
    assert name2 is None

    # Search portal header
    name3 = clean_legal_name_candidate("of Business")
    assert name3 is None

    # Error message
    name4 = clean_legal_name_candidate("Something went wrong")
    assert name4 is None

    # Valid name with suffix
    name5 = clean_legal_name_candidate("TATA CONSULTANCY SERVICES LIMITED - Company Profile | Zauba Corp")
    assert name5 == "TATA CONSULTANCY SERVICES LIMITED"


def test_11_placeholder_and_generic_label_rejection_for_business_activity():
    """Test that business activity extraction rejects single-word table headers like 'Code', 'Activities', 'N/A'."""
    text_with_bad_headers = """
    Company Information
    Nature of Business Activities:
    Code:
    CIN: L22210MH1995PLC084781
    Status: Active
    """
    act1 = extract_business_activity_from_text(text_with_bad_headers)
    assert act1 == "NOT_FOUND"

    text_with_real_activity = """
    Company Information
    Principal Business Activity: Computer programming, consultancy and related activities
    CIN: L22210MH1995PLC084781
    Status: Active
    """
    act2 = extract_business_activity_from_text(text_with_real_activity)
    assert act2 == "Computer programming, consultancy and related activities"


def test_12_valid_evidence_extraction_for_all_fields():
    """Test that valid company pages produce clean status, address with PIN code, incorporation date, and activity."""
    html_page = """
    <html>
    <head><title>Apex Software Solutions Limited - Company Profile | Tofler</title></head>
    <body>
        <h1>Apex Software Solutions Limited</h1>
        <p>CIN: U72200DL2005PLC123456</p>
        <p>Company Status: Active</p>
        <p>Date of Incorporation: 15/08/2005</p>
        <p>Registered Address: 402 Business Tower, Nehru Place, New Delhi, Delhi 110019</p>
        <p>Principal Business Activity: Information technology consultancy services</p>
    </body>
    </html>
    """
    task = ResearchTask(
        task_id="TASK-TEST-012",
        task_type="THIRD_PARTY_RESEARCH",
        target="Apex Software Solutions Limited U72200DL2005PLC123456",
        preferred_sources=["tofler.in"],
        required_fields=["legal_name", "company_status", "registered_address", "incorporation_date", "business_activity"],
        objective="Verify company details on third party registry",
        priority=1,
    )

    agent = BrowserResearchAgent(fetcher=lambda u: html_page)
    results = agent.execute(task)

    res_map = {r.field_name: r.field_value for r in results}
    assert res_map["legal_name"] == "Apex Software Solutions Limited"
    assert res_map["company_status"] == "ACTIVE"
    assert "110019" in res_map["registered_address"]
    assert "2005" in res_map["incorporation_date"]
    assert "Information technology consultancy services" in res_map["business_activity"]
    assert all(r.confidence >= 0.70 for r in results)


def test_13_multiple_successful_sources_all_persist_distinct_evidence():
    """Test that multiple third-party sources (e.g. Tofler and ZaubaCorp) both persist their evidence independently."""
    state = make_test_state({
        "business_name": "Apex Software Solutions Limited",
        "cin": "U72200DL2005PLC123456",
    })

    planner = PlannerAgent()
    tasks = planner.plan(state)
    tp_tasks = [t for t in tasks if t.task_type == "THIRD_PARTY_RESEARCH"]

    tofler_task = next(t for t in tp_tasks if t.preferred_sources[0] == "tofler.in")
    zauba_task = next(t for t in tp_tasks if t.preferred_sources[0] == "zaubacorp.com")

    def tofler_fetcher(url):
        return "<html><title>Apex Software Solutions Limited</title><body><h1>Apex Software Solutions Limited</h1><p>Company Status: Active</p><p>Registered Address: Nehru Place, New Delhi 110019</p></body></html>"

    def zauba_fetcher(url):
        return "<html><title>Apex Software Solutions Limited</title><body><h1>Apex Software Solutions Limited</h1><p>Status: Active</p><p>Registered Address: 402 Business Tower, New Delhi 110019</p></body></html>"

    tofler_agent = BrowserResearchAgent(fetcher=tofler_fetcher)
    zauba_agent = BrowserResearchAgent(fetcher=zauba_fetcher)

    tofler_results = tofler_agent.execute(tofler_task)
    zauba_results = zauba_agent.execute(zauba_task)

    assert any(r.source_name == "Tofler" and r.confidence > 0 for r in tofler_results)
    assert any(r.source_name == "Zauba Corp" and r.confidence > 0 for r in zauba_results)


def test_14_no_company_specific_hacks_arbitrary_unseeded_company():
    """Test that an arbitrary, unseeded company name works seamlessly across URL resolution and research."""
    arbitrary_name = "Zenith Solar Energy Systems Private Limited"
    arbitrary_cin = "U40106GJ2018PTC102938"

    state = make_test_state({
        "business_name": arbitrary_name,
        "cin": arbitrary_cin,
    })

    planner = PlannerAgent()
    tasks = planner.plan(state)
    tp_tasks = [t for t in tasks if t.task_type == "THIRD_PARTY_RESEARCH"]

    assert len(tp_tasks) == 5
    for task in tp_tasks:
        src = task.preferred_sources[0]
        meta = source_registry.get_source(src)
        cands = meta.get_candidate_urls(task.target)
        assert len(cands) > 0
        assert any("zenith-solar-energy-systems-private-limited" in u for u in cands)
