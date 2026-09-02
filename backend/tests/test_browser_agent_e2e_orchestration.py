import time
import pytest
from unittest import mock
from datetime import datetime, timezone
import uuid

from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask, ResearchResult
from app.research.source_registry import source_registry
from app.research.dispatcher import ResearchDispatcher


# ==============================================================================
# SCENARIO A: Company name + City + Known Website
# ==============================================================================

def test_scenario_a_company_name_and_website_orchestration():
    agent = BrowserResearchAgent()
    
    website_task = ResearchTask(
        task_id="TASK-WEB-01",
        task_type="WEBSITE_VERIFICATION",
        target="https://acmefoods.in",
        objective="Verify official website and business operations",
        required_fields=["page_title", "website_status", "business_activity", "contact_address"],
        priority=1,
        preferred_sources=["company_website"],
    )

    def mock_fetcher(url: str) -> str:
        if "acmefoods.in" in url:
            return """
            <html>
              <head><title>Acme Foods India Private Limited - Official Site</title></head>
              <body>
                <h1>Welcome to Acme Foods</h1>
                <p>Business Activity: Manufacturing and processing of premium organic food products.</p>
                <p>Registered Office: 42 Industrial Area, Phase II, Bengaluru, Karnataka 560058</p>
                <p>Contact: info@acmefoods.in | Phone: +91-80-12345678</p>
              </body>
            </html>
            """
        raise ValueError(f"Unexpected URL: {url}")

    agent.fetcher = mock_fetcher
    agent.dispatcher.fetcher = mock_fetcher

    results = agent.execute(website_task)
    assert len(results) == 4
    res_map = {r.field_name: r for r in results}

    assert res_map["website_status"].field_value == "AVAILABLE"
    assert res_map["website_status"].verification_status == "VERIFIED"
    assert res_map["website_status"].authority_tier == 2
    assert "Acme Foods" in res_map["page_title"].field_value
    assert "Manufacturing and processing" in res_map["business_activity"].field_value


# ==============================================================================
# SCENARIO B: Company name + GSTIN
# ==============================================================================

def test_scenario_b_gstin_lookup_orchestration():
    agent = BrowserResearchAgent()
    
    gst_task = ResearchTask(
        task_id="TASK-GST-01",
        task_type="GST_VERIFICATION",
        target="29AAACW0387R1Z6",
        objective="Verify GSTIN status and taxpayer master details",
        required_fields=["legal_name", "gst_status", "registered_address", "business_activity"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=["quickcompany.in"],
    )

    def mock_fetcher(url: str) -> str:
        if "gst.gov.in" in url:
            return """
            <html>
              <head><title>Search Taxpayer - GST</title></head>
              <body>
                <div>GSTIN: 29AAACW0387R1Z6</div>
                <div>Legal Name of Business: Apex Cloud Systems Private Limited</div>
                <div>GSTIN / UIN Status: Active</div>
                <div>Principal Place of Business: 100 Outer Ring Road, Bellandur, Bengaluru 560103</div>
                <div>Business Activity: Cloud hosting and managed infrastructure services</div>
              </body>
            </html>
            """
        raise ValueError(f"Unexpected URL: {url}")

    agent.fetcher = mock_fetcher
    agent.dispatcher.fetcher = mock_fetcher

    results = agent.execute(gst_task)
    assert len(results) == 4
    res_map = {r.field_name: r for r in results}

    assert res_map["gst_status"].field_value in {"ACTIVE", "AVAILABLE"}
    assert res_map["gst_status"].verification_status == "VERIFIED"
    assert res_map["gst_status"].authority_tier == 1
    assert "Apex Cloud Systems" in res_map["legal_name"].field_value
    assert "Outer Ring Road" in res_map["registered_address"].field_value


# ==============================================================================
# SCENARIO C: Company name + CIN (MCA Verification)
# ==============================================================================

def test_scenario_c_cin_lookup_orchestration():
    agent = BrowserResearchAgent()
    
    mca_task = ResearchTask(
        task_id="TASK-MCA-01",
        task_type="MCA_VERIFICATION",
        target="U72200KA2020PTC123456",
        objective="Verify MCA corporate master data",
        required_fields=["legal_name", "company_status", "incorporation_date", "registered_address"],
        priority=1,
        preferred_sources=["mca.gov.in"],
        fallback_sources=["quickcompany.in"],
    )

    def mock_fetcher(url: str) -> str:
        if "quickcompany" in url:
            return """
            <html>
              <head><title>Apex Cloud Systems Private Limited Details | QuickCompany</title></head>
              <body>
                <div>Company Name: Apex Cloud Systems Private Limited</div>
                <div>CIN: U72200KA2020PTC123456</div>
                <div>Company Status: Active</div>
                <div>Date of Incorporation: 15 March 2020</div>
                <div>Registered Address: 100 Outer Ring Road, Bellandur, Bengaluru, Karnataka, 560103</div>
              </body>
            </html>
            """
        return ""

    agent.fetcher = mock_fetcher
    agent.dispatcher.fetcher = mock_fetcher

    results = agent.execute(mca_task)
    assert len(results) == 4
    res_map = {r.field_name: r for r in results}

    assert res_map["company_status"].field_value == "ACTIVE"
    assert res_map["company_status"].verification_status == "VERIFIED"
    assert res_map["company_status"].authority_tier == 3
    assert "Apex Cloud Systems" in res_map["legal_name"].field_value
    assert "2020" in res_map["incorporation_date"].field_value


# ==============================================================================
# SCENARIO D: Primary Government Source Blocked -> Automatic Fallback
# ==============================================================================

def test_scenario_d_primary_gov_blocked_falls_back_to_tier3():
    agent = BrowserResearchAgent()
    
    gst_task = ResearchTask(
        task_id="TASK-GST-FB",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status with fallback",
        required_fields=["legal_name", "gst_status", "registered_address"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=["quickcompany.in", "zaubacorp.com"],
    )

    def mock_fetcher(url: str) -> str:
        if "gst.gov.in" in url:
            # Returns Cloudflare 403 access denied error
            return "<html><title>Access Denied</title><body>403 Forbidden Cloudflare Ray ID access denied security check</body></html>"
        elif "quickcompany.in" in url:
            return """
            <html>
              <head><title>GST 27AAACW0387R1Z6 - Wipro Limited</title></head>
              <body>
                <div>GSTIN: 27AAACW0387R1Z6</div>
                <div>Legal Name: Wipro Limited</div>
                <div>GST Status: Active</div>
                <div>Address: Doddakannelli, Sarjapur Road, Bengaluru 560035</div>
              </body>
            </html>
            """
        raise ValueError(f"Unexpected URL: {url}")

    agent.fetcher = mock_fetcher
    agent.dispatcher.fetcher = mock_fetcher

    results = agent.execute(gst_task)
    assert len(results) == 3
    res_map = {r.field_name: r for r in results}

    # Proves fallback succeeded honestly without human intervention
    assert res_map["gst_status"].field_value in {"ACTIVE", "AVAILABLE"}
    assert res_map["gst_status"].source_name in {"QuickCompany", "Third-Party Source", "quickcompany.in"}
    assert res_map["gst_status"].authority_tier == 3
    assert 0.50 <= res_map["gst_status"].confidence <= 0.85
    assert "Wipro Limited" in res_map["legal_name"].field_value


# ==============================================================================
# SCENARIO E: All Sources Blocked / Unavailable -> Honest SOURCE_UNAVAILABLE
# ==============================================================================

def test_scenario_e_all_sources_blocked_honest_failure():
    agent = BrowserResearchAgent()
    
    gst_task = ResearchTask(
        task_id="TASK-GST-FAIL",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST status",
        required_fields=["legal_name", "gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=["quickcompany.in"],
    )

    def mock_fetcher(url: str) -> str:
        # All URLs return 404 or 403
        return "<html><title>404 Not Found</title><body>The requested page could not be found.</body></html>"

    agent.fetcher = mock_fetcher
    agent.dispatcher.fetcher = mock_fetcher

    results = agent.execute(gst_task)
    assert len(results) == 2
    for r in results:
        assert r.confidence == 0.0
        assert r.verification_status in {"SOURCE_UNAVAILABLE", "NOT_FOUND"}
        assert r.field_value in {"NOT_FOUND", "UNAVAILABLE", None}
        assert r.evidence_basis is not None


# ==============================================================================
# CONCURRENCY: Independent Tasks Run In Parallel Without Blocking
# ==============================================================================

def test_browser_agent_concurrency_execution():
    dispatcher = ResearchDispatcher()

    task1 = ResearchTask(
        task_id="T1-GST", task_type="GST_VERIFICATION", target="27AAACW0387R1Z6",
        objective="GST", required_fields=["gst_status"], priority=1, preferred_sources=["gst.gov.in"]
    )
    task2 = ResearchTask(
        task_id="T2-MCA", task_type="MCA_VERIFICATION", target="U72200KA2020PTC123456",
        objective="MCA", required_fields=["company_status"], priority=1, preferred_sources=["mca.gov.in"]
    )
    task3 = ResearchTask(
        task_id="T3-WEB", task_type="WEBSITE_VERIFICATION", target="https://wipro.com",
        objective="Web", required_fields=["website_status"], priority=1, preferred_sources=["company_website"]
    )

    def delayed_fetcher(url: str) -> str:
        time.sleep(0.20)
        return "<html><title>Status Active</title><body>GSTIN: 27AAACW0387R1Z6 Status: Active</body></html>"

    start_time = time.time()
    results_map = dispatcher.execute_tasks_concurrent([task1, task2, task3], max_workers=3, fetcher=delayed_fetcher)
    elapsed = time.time() - start_time

    assert len(results_map) == 3
    assert "T1-GST" in results_map
    assert "T2-MCA" in results_map
    assert "T3-WEB" in results_map
    # 3 tasks * 0.20s sequentially would take > 0.60s. Concurrently should take < 0.35s
    assert elapsed < 0.40


# ==============================================================================
# FAILURE ISOLATION: Single Task Failure Does Not Break Other Tasks
# ==============================================================================

def test_failure_isolation_across_multiple_tasks():
    dispatcher = ResearchDispatcher()

    task_failing = ResearchTask(
        task_id="T-FAIL", task_type="GST_VERIFICATION", target="99INVALIDGSTIN",
        objective="GST", required_fields=["gst_status"], priority=1, preferred_sources=["gst.gov.in"]
    )
    task_succeeding = ResearchTask(
        task_id="T-SUCC", task_type="WEBSITE_VERIFICATION", target="https://safe-business.com",
        objective="Web", required_fields=["website_status"], priority=1, preferred_sources=["company_website"]
    )

    def mock_fetcher(url: str) -> str:
        if "gst.gov.in" in url:
            raise ConnectionResetError("Remote server closed connection")
        return "<html><title>Safe Business</title><body>Safe Business Home</body></html>"

    results_map = dispatcher.execute_tasks_concurrent([task_failing, task_succeeding], max_workers=2, fetcher=mock_fetcher)

    assert len(results_map) == 2
    # Failing task returns honest failure without crashing the batch
    fail_res = results_map["T-FAIL"]
    assert len(fail_res) == 1
    assert fail_res[0].confidence == 0.0
    assert fail_res[0].verification_status == "SOURCE_UNAVAILABLE"

    # Succeeding task returns verified result
    succ_res = results_map["T-SUCC"]
    assert len(succ_res) == 1
    assert succ_res[0].field_value == "AVAILABLE"
    assert succ_res[0].verification_status == "VERIFIED"


# ==============================================================================
# SEARCH-ENGINE INDEPENDENCE: No Calls to Search Engines During Normal Research
# ==============================================================================

def test_search_engine_independence():
    agent = BrowserResearchAgent()
    invoked_urls = []

    def mock_fetcher(url: str) -> str:
        invoked_urls.append(url)
        return "<html><title>Portal</title><body>Legal Name: Verified Corp Active</body></html>"

    agent.fetcher = mock_fetcher
    agent.dispatcher.fetcher = mock_fetcher

    task = ResearchTask(
        task_id="TASK-DIRECT",
        task_type="GST_VERIFICATION",
        target="27AAACW0387R1Z6",
        objective="Verify GST",
        required_fields=["legal_name", "gst_status"],
        priority=1,
        preferred_sources=["gst.gov.in"],
        fallback_sources=["quickcompany.in"],
    )

    results = agent.execute(task)
    assert len(results) == 2

    # Verify that NO search engine URLs were contacted
    search_engines = ["google.com", "bing.com", "duckduckgo.com", "yahoo.com"]
    for url in invoked_urls:
        for engine in search_engines:
            assert engine not in url.lower(), f"Search engine {engine} was invoked in URL: {url}"
