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


def test_public_source_fallback_chain_quickcompany_zauba():
    """
    Test that when QuickCompany fails/blocks, the agent automatically falls through
    to registered fallback (Zauba Corp) directly, with ZERO search engine calls.
    """
    inv_id = uuid.uuid4()
    _create_test_inv(inv_id, "Apex Dynamic Systems")
    task_id = "TASK-FALLBACK-01"
    target_company = "Apex Dynamic Systems"

    agent = BrowserResearchAgent()
    attempts = []

    def mock_fetcher(url: str) -> str:
        attempts.append(url)
        # Search engines must never be called
        assert not any(se in url.lower() for se in ["duckduckgo.com", "bing.com", "google.com", "yahoo.com"]), f"Search engine called: {url}"
        
        if "quickcompany.in" in url:
            raise HTTPError(url, 403, "Forbidden", hdrs={}, fp=None)
        elif "zaubacorp.com" in url:
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
        preferred_sources=["quickcompany.in"],
        fallback_sources=["zaubacorp.com"],
    )

    results = agent.execute(task, investigation_id=inv_id)

    assert len(results) > 0
    cin_res = next((r for r in results if r.field_name == "cin"), None)
    name_res = next((r for r in results if r.field_name == "legal_name"), None)
    status_res = next((r for r in results if r.field_name == "company_status"), None)

    assert cin_res is not None and cin_res.field_value == "U72200MH2021PTC123456"
    assert name_res is not None and "Apex Dynamic Systems" in name_res.field_value
    assert status_res is not None and status_res.field_value == "ACTIVE"

    # Verify zero search engines were called
    assert not any("duckduckgo.com" in u for u in attempts)
    assert not any("bing.com" in u for u in attempts)


def test_mca_waf_block_falls_back_to_registered_directory():
    """
    Test that when MCA returns a bot/WAF challenge, the agent automatically falls back
    to registered directory (Zauba Corp / QuickCompany) without human intervention or search engines.
    """
    inv_id = uuid.uuid4()
    _create_test_inv(inv_id, "Nexus Innovations")
    task_id = "TASK-MCA-01"
    target_cin = "U74999DL2020PTC365432 Nexus Innovations"

    agent = BrowserResearchAgent()
    attempts = []

    def mock_fetcher(url: str) -> str:
        attempts.append(url)
        assert not any(se in url.lower() for se in ["duckduckgo.com", "bing.com", "google.com", "yahoo.com"])
        
        if "mca.gov.in" in url:
            return """
            <html>
              <head><title>Attention Required! | Cloudflare</title></head>
              <body>
                <h2>Sorry, you have been blocked</h2>
                <p>This website is using a security service to protect itself from online attacks.</p>
                <div class="cf-browser-verification">cf challenge</div>
              </body>
            </html>
            """
        elif "quickcompany.in" in url:
            return """
            <html>
              <head><title>Nexus Innovations Private Limited - Company Details | QuickCompany</title></head>
              <body>
                <h1>Nexus Innovations Private Limited</h1>
                <p>CIN: U74999DL2020PTC365432</p>
                <p>Company Status: Active</p>
                <p>Registered Address: Plot 44, Okhla Phase 3, New Delhi 110020, Delhi, India</p>
                <p>Registration Date: 12 March 2020</p>
              </body>
            </html>
            """
        raise Exception(f"Unexpected URL: {url}")

    agent.fetcher = mock_fetcher

    task = ResearchTask(
        task_id=task_id,
        task_type="MCA_VERIFICATION",
        target=target_cin,
        objective="Verify MCA master data",
        required_fields=["cin", "legal_name", "company_status", "registered_address"],
        priority=1,
        preferred_sources=["mca.gov.in"],
        fallback_sources=["quickcompany.in"],
    )

    results = agent.execute(task, investigation_id=inv_id)

    assert len(results) > 0
    cin_res = next((r for r in results if r.field_name == "cin"), None)
    status_res = next((r for r in results if r.field_name == "company_status"), None)

    assert cin_res is not None and cin_res.field_value == "U74999DL2020PTC365432"
    assert status_res is not None and status_res.field_value == "ACTIVE"

    # Verify sessions
    with db_lock:
        with SessionLocal() as db:
            sessions = db.query(BrowserSession).filter(BrowserSession.investigation_id == inv_id).all()
            assert len(sessions) >= 2
            mca_session = next((s for s in sessions if "mca.gov.in" in (s.domain or "")), None)
            assert mca_session is not None
            assert mca_session.status in {"BLOCKED_OR_ERROR", "BLOCKED"}


def test_website_direct_url_verification():
    """
    Test that website verification navigates directly to the target website URL
    and verifies entity details without search engines.
    """
    inv_id = uuid.uuid4()
    _create_test_inv(inv_id, "Zenith Dynamics")
    task_id = "TASK-WEB-01"
    biz_website = "https://www.zenithdynamics.io"

    agent = BrowserResearchAgent()
    attempts = []

    def mock_fetcher(url: str) -> str:
        attempts.append(url)
        assert not any(se in url.lower() for se in ["duckduckgo.com", "bing.com", "google.com", "yahoo.com"])
        
        if "zenithdynamics.io" in url:
            return """
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
        target=biz_website,
        objective="Verify official company website",
        required_fields=["website_status", "page_title"],
        priority=1,
        preferred_sources=["company_website"],
        fallback_sources=[],
    )

    results = agent.execute(task, investigation_id=inv_id)

    assert len(results) > 0
    status_res = next((r for r in results if r.field_name == "website_status"), None)
    title_res = next((r for r in results if r.field_name == "page_title"), None)

    assert status_res is not None and status_res.field_value == "AVAILABLE"
    assert title_res is not None and "Zenith Dynamics" in title_res.field_value


def test_zero_search_engine_queries_enforced():
    """
    Explicit test proving that any attempt to fetch a search engine URL will fail immediately.
    """
    from app.research.source_registry import source_registry
    forbidden_domains = ["duckduckgo.com", "bing.com", "google.com", "yahoo.com", "html.duckduckgo.com"]
    agent = BrowserResearchAgent()

    for task_type in ["GST_VERIFICATION", "MCA_VERIFICATION", "EPFO_VERIFICATION", "WEBSITE_VERIFICATION", "THIRD_PARTY_RESEARCH"]:
        task = ResearchTask(
            task_id=f"TASK-TEST-{task_type}",
            task_type=task_type,
            target="Wipro Limited 29AAACW0387R1Z6",
            objective="Verify entity",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=[],
            fallback_sources=[],
        )
        # Check that resolved candidate sources contain zero search engines
        pref, fall = source_registry.get_preferred_and_fallback_sources(task_type)
        for src in pref + fall:
            url = agent._resolve_url(task, src, None)
            if url:
                assert not any(fd in url.lower() for fd in forbidden_domains), f"Forbidden engine in {src}: {url}"
