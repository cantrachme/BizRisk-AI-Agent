import uuid
import pytest
from unittest import mock
from datetime import datetime, timezone
from urllib.error import HTTPError

from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask
from app.models.browser_session import BrowserSession
from app.models.investigation import Investigation
from app.db.session import SessionLocal, db_lock

def _create_test_inv(inv_id: uuid.UUID, biz_name: str):
    with db_lock:
        with SessionLocal() as db:
            inv = Investigation(
                id=inv_id,
                user_id="test-user",
                status="IN_PROGRESS",
                input_data=f'{{"business_name": "{biz_name}"}}',
                created_at=datetime.now(timezone.utc),
            )
            db.add(inv)
            db.commit()

def test_search_engine_chain_ddg_exception_bing_success():
    """
    Test that when DuckDuckGo raises a Fetch Exception (e.g. HTTP 403 / Timeout),
    the agent does NOT terminate the task, but falls through to DDG HTML and Bing,
    discovers candidate links from Bing, navigates to the result page, and extracts evidence.
    """
    inv_id = uuid.uuid4()
    _create_test_inv(inv_id, "Apex Dynamic Systems")
    task_id = "TASK-FALLBACK-01"
    target_company = "Apex Dynamic Systems"

    agent = BrowserResearchAgent()

    attempts = []

    def mock_fetcher(url: str) -> str:
        attempts.append(url)
        if "html.duckduckgo.com" in url:
            # Secondary engine also fails
            raise Exception("Connection reset by peer")
        elif "duckduckgo.com" in url:
            # Primary engine raises Fetch Exception
            raise HTTPError(url, 403, "Forbidden", hdrs={}, fp=None)
        elif "bing.com" in url:
            # Tertiary engine Bing succeeds and returns candidate links
            return f"""
            <html>
              <head><title>Bing Search - {target_company}</title></head>
              <body>
                <ol id="b_results">
                  <li>
                    <h2><a href="https://www.zaubacorp.com/company/Apex-Dynamic-Systems/U72200MH2021PTC123456">Apex Dynamic Systems ZaubaCorp</a></h2>
                  </li>
                </ol>
              </body>
            </html>
            """
        elif "zaubacorp.com" in url:
            # Actual opened result page
            return f"""
            <html>
              <head><title>Apex Dynamic Systems Private Limited - Company Details | Zauba Corp</title></head>
              <body>
                <div>Company Details</div>
                <div>CIN: U72200MH2021PTC123456</div>
                <div>Company Name: Apex Dynamic Systems Private Limited</div>
                <div>Company Status: Active</div>
                <div>RoC: RoC-Mumbai</div>
                <div>Registration Number: 123456</div>
                <div>Company Category: Company limited by Shares</div>
                <div>Class of Company: Private</div>
                <div>Date of Incorporation: 15 January 2021</div>
                <div>Authorized Capital: 1000000</div>
                <div>Paid up capital: 500000</div>
                <div>Registered Address: 101 Tech Hub, BKC, Mumbai 400051, Maharashtra, India</div>
              </body>
            </html>
            """
        raise Exception(f"Unexpected URL requested: {url}")

    agent.fetcher = mock_fetcher

    task = ResearchTask(
        task_id=task_id,
        task_type="THIRD_PARTY_RESEARCH",
        target=target_company,
        objective="Verify company details on third-party registry",
        required_fields=["cin", "legal_name", "company_status"],
        priority=1,
        preferred_sources=["third_party"],
        fallback_sources=[],
    )

    results = agent.execute(task, investigation_id=inv_id)

    assert len(results) > 0
    # Evidence must come from the actual result page, not the search engine
    cin_res = next((r for r in results if r.field_name == "cin"), None)
    assert cin_res is not None
    assert cin_res.field_value == "U72200MH2021PTC123456"
    assert cin_res.confidence > 0.0
    assert "zaubacorp.com" in cin_res.source_url

    # Verify that all 3 search engine attempts were tried in sequence
    assert any("duckduckgo.com/?q=" in u for u in attempts)
    assert any("html.duckduckgo.com" in u for u in attempts)
    assert any("bing.com/search" in u for u in attempts)
    assert any("zaubacorp.com" in u for u in attempts)

    # Verify BrowserSession attempts in database
    with db_lock:
        with SessionLocal() as db:
            sessions = db.query(BrowserSession).filter(
                BrowserSession.investigation_id == inv_id,
                BrowserSession.task_id == task_id
            ).order_by(BrowserSession.started_at.asc()).all()

            # Should have DDG (ERROR), DDG HTML (ERROR), Bing (SUCCESS), ZaubaCorp (SUCCESS)
            assert len(sessions) >= 3
            ddg_session = next((s for s in sessions if "duckduckgo.com/?q=" in (s.failure_reason or "") or s.domain == "duckduckgo.com"), None)
            assert ddg_session is not None
            assert ddg_session.status in {"ERROR", "BLOCKED"}
            assert ddg_session.action_count == 1

            zauba_session = next((s for s in sessions if "zaubacorp.com" in s.domain or "zaubacorp" in (s.failure_reason or "")), None)
            assert zauba_session is not None
            assert zauba_session.status == "SUCCESS"

def test_mca_waf_block_falls_back_to_search_chain_and_third_party():
    """
    Test that when MCA official portal fails with WAF / Fetch Exception,
    the task transitions to third_party fallback, which uses the search chain
    to find registry evidence.
    """
    inv_id = uuid.uuid4()
    _create_test_inv(inv_id, "Apex Dynamic Systems")
    task_id = "TASK-MCA-01"
    target_cin = "U72200MH2021PTC123456"

    agent = BrowserResearchAgent()

    attempts = []

    def mock_fetcher(url: str) -> str:
        attempts.append(url)
        if "mca.gov.in" in url:
            # MCA portal blocked / 503
            raise Exception("503 Service Unavailable (WAF Challenge)")
        elif "duckduckgo.com/?q=" in url:
            # DDG fails
            raise Exception("Timeout Error on DuckDuckGo")
        elif "html.duckduckgo.com" in url:
            # DDG HTML succeeds
            return f"""
            <html>
              <head><title>DuckDuckGo HTML</title></head>
              <body>
                <a class="result__url" href="https://www.tofler.in/apex-dynamic-systems/{target_cin}">Tofler Link</a>
              </body>
            </html>
            """
        elif "tofler.in" in url:
            return f"""
            <html>
              <head><title>Apex Dynamic Systems - Tofler</title></head>
              <body>
                <div>Company Details</div>
                <div>CIN: {target_cin}</div>
                <div>Legal Name: Apex Dynamic Systems Private Limited</div>
                <div>Company Status: Active</div>
                <div>Authorized Capital: 1000000</div>
              </body>
            </html>
            """
        raise Exception(f"Unknown URL: {url}")

    agent.fetcher = mock_fetcher

    task = ResearchTask(
        task_id=task_id,
        task_type="MCA_VERIFICATION",
        target=target_cin,
        objective="Verify MCA incorporation status and CIN",
        required_fields=["cin", "legal_name", "company_status"],
        priority=1,
        preferred_sources=["mca.gov.in"],
        fallback_sources=["third_party"],
    )

    results = agent.execute(task, investigation_id=inv_id)

    assert len(results) > 0
    cin_res = next((r for r in results if r.field_name == "cin"), None)
    assert cin_res is not None
    assert cin_res.field_value == target_cin

    assert any("mca.gov.in" in u for u in attempts)
    assert any("duckduckgo.com" in u for u in attempts)
    assert any("tofler.in" in u for u in attempts)

def test_website_discovery_filters_third_party_aggregators():
    """
    Test that website verification discovery ignores ZaubaCorp/Tofler and only opens official domains.
    """
    inv_id = uuid.uuid4()
    _create_test_inv(inv_id, "Zenith Dynamics")
    task_id = "TASK-WEB-01"
    biz_name = "Zenith Dynamics"

    agent = BrowserResearchAgent()

    attempts = []

    def mock_fetcher(url: str) -> str:
        attempts.append(url)
        if "duckduckgo.com/?q=" in url:
            raise Exception("DDG Fetch Exception")
        elif "html.duckduckgo.com" in url:
            # Returns mixed candidate links (aggregators + official company site)
            return f"""
            <html>
              <head><title>DuckDuckGo HTML - {biz_name}</title></head>
              <body>
                <a href="https://www.zaubacorp.com/company/Zenith-Dynamics/123">Zauba Link</a>
                <a href="https://www.tofler.in/zenith-dynamics/123">Tofler Link</a>
                <a href="https://www.zenithdynamics.io">Official Zenith Dynamics Website</a>
              </body>
            </html>
            """
        elif "zenithdynamics.io" in url:
            return f"""
            <html>
              <head><title>Zenith Dynamics | Official Cloud Software</title></head>
              <body>
                <h1>Zenith Dynamics</h1>
                <p>Welcome to Zenith Dynamics official portal. We provide AI enterprise solutions.</p>
                <footer>Contact: support@zenithdynamics.io | Mumbai, India</footer>
              </body>
            </html>
            """
        raise Exception(f"Unknown URL: {url}")

    agent.fetcher = mock_fetcher

    task = ResearchTask(
        task_id=task_id,
        task_type="WEBSITE_VERIFICATION",
        target=biz_name,
        objective="Verify official company website",
        required_fields=["website_url", "title", "meta_description"],
        priority=1,
        preferred_sources=["company_website"],
        fallback_sources=["generic_web"],
    )

    results = agent.execute(task, investigation_id=inv_id)

    assert len(results) > 0
    web_res = next((r for r in results if r.field_name == "website_url"), None)
    assert web_res is not None
    assert "zenithdynamics.io" in web_res.field_value
    assert "zaubacorp.com" not in web_res.field_value
    assert "tofler.in" not in web_res.field_value
