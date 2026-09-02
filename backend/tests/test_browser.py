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
    assert results[0].source_url in {"https://services.gst.gov.in/services/searchtp", "https://www.gst.gov.in"}
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
    assert results[0].field_value == "NOT_FOUND"
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
    assert analysis["overall_risk"]["score"] is None


def test_fallback_sources_execution():
    # Primary is gst.gov.in (will return blocked page), Fallback is third_party (will return valid page)
    task = make_task(
        preferred_sources=["gst.gov.in"],
        fallback_sources=["third_party"],
        target="27ABCDE1234F1Z5",
    )

    def fetcher(url: str) -> str:
        if "gst.gov.in" in url:
            return "<html><title>Access Denied</title><body>403 Forbidden cloudflare security check.</body></html>"
        elif "quickcompany" in url or "third_party" in url:
            return "<html><title>QuickCompany Profile</title><body>GSTIN: 27ABCDE1234F1Z5 is active. ABC Foods Private Limited.</body></html>"
        raise ValueError(f"Unknown URL: {url}")

    agent = BrowserResearchAgent(fetcher=fetcher)
    results = agent.execute(task)

    # Since primary failed/blocked, it fell back to third_party and succeeded!
    assert len(results) == 2
    assert results[0].source_name == "Third-Party Source"
    assert results[0].confidence == 0.50
    assert results[1].field_value == "AVAILABLE"


def test_browser_sessions_structured_attempts():
    import json
    from datetime import timezone
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    from app.models.browser_session import BrowserSession
    from app.models.investigation import Investigation
    
    # 1. Setup in-memory SQLite DB
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )
    
    db = TestingSessionLocal()
    try:
        # Create investigation
        inv = Investigation(
            status="created",
            input_data='{"gstin": "27ABCDE1234F1Z5"}',
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)
        
        task = make_task(
            preferred_sources=["gst.gov.in"],
            fallback_sources=["third_party"],
            target="27ABCDE1234F1Z5",
        )

        def fetcher(url: str) -> str:
            if "gst.gov.in" in url:
                return "<html><title>Access Denied</title><body>403 Forbidden cloudflare security check.</body></html>"
            elif "quickcompany" in url or "third_party" in url:
                return "<html><title>QuickCompany Profile</title><body>GSTIN: 27ABCDE1234F1Z5 is active. ABC Foods Private Limited.</body></html>"
            raise ValueError(f"Unknown URL: {url}")

        agent = BrowserResearchAgent(fetcher=fetcher)
        
        # Patch SessionLocal to use our TestingSessionLocal in BrowserResearchAgent
        with patch("app.db.session.SessionLocal", TestingSessionLocal):
            results = agent.execute(task, investigation_id=inv.id)
        
        # 2. Query BrowserSession records
        sessions = db.query(BrowserSession).filter(BrowserSession.investigation_id == inv.id).all()
        assert len(sessions) == 2
        
        # Sort by attempt_order
        sessions = sorted(sessions, key=lambda s: json.loads(s.failure_reason)["attempt_order"])
        
        s1 = sessions[0]
        assert s1.domain == "gst.gov.in"
        assert s1.status == "BLOCKED_OR_ERROR"
        m1 = json.loads(s1.failure_reason)
        assert m1["source_name"] == "GST Portal"
        assert m1["attempt_order"] == 1
        assert m1["selected_as_evidence"] is False
        
        s2 = sessions[1]
        assert s2.domain == "third_party"
        assert s2.status == "SUCCESS"
        m2 = json.loads(s2.failure_reason)
        assert m2["source_name"] == "Third-Party Source"
        assert m2["attempt_order"] == 2
        assert m2["selected_as_evidence"] is True
        
    finally:
        db.close()


def test_resolve_url_returns_raw_urls_and_preserves_explicit_urls():
    """A & B: _resolve_url returns raw URLs and preserves explicit company URLs."""
    task_gst = make_task(task_type="GST_VERIFICATION", target="29AAACW0387R1Z6")
    url_gst = BrowserResearchAgent._resolve_url(task_gst, "gst.gov.in", None)
    assert url_gst == "https://services.gst.gov.in/services/searchtp"
    assert not url_gst.startswith("[")
    assert "](" not in url_gst

    task_mca = make_task(task_type="MCA_VERIFICATION", target="L32102KA1945PLC020800")
    url_mca = BrowserResearchAgent._resolve_url(task_mca, "mca.gov.in", None)
    assert url_mca == "https://www.mca.gov.in"
    assert not url_mca.startswith("[")

    task_web = make_task(task_type="WEBSITE_VERIFICATION", target="https://www.wipro.com")
    url_web = BrowserResearchAgent._resolve_url(task_web, "company_website", None)
    assert url_web == "https://www.wipro.com"
    assert not url_web.startswith("[")


def test_no_search_engine_calls_in_execute():
    """C: execute never calls duckduckgo, bing, google, or yahoo."""
    called_urls = []

    def tracking_fetcher(url: str) -> str:
        called_urls.append(url)
        return "<html><title>Wipro</title><body>Wipro Limited official content</body></html>"

    agent = BrowserResearchAgent(fetcher=tracking_fetcher)
    task = make_task(
        task_type="WEBSITE_VERIFICATION",
        target="https://www.wipro.com",
        preferred_sources=["company_website"],
        fallback_sources=["generic_web"],
        required_fields=["website_status", "page_title"],
    )
    results = agent.execute(task)
    assert len(results) >= 1
    for url in called_urls:
        assert not any(se in url.lower() for se in ["duckduckgo", "bing", "google", "yahoo"])


def test_captcha_autonomous_handling_no_exception():
    """D & E: CAPTCHA does not raise HumanInterventionRequiredException, produces confidence 0, selected_as_evidence false."""
    agent = BrowserResearchAgent(fetcher=lambda u: "<html><title>Please verify you are human</title><body>recaptcha challenge</body></html>")
    task = make_task(
        task_type="GST_VERIFICATION",
        target="29AAACW0387R1Z6",
        preferred_sources=["gst.gov.in"],
        fallback_sources=[],
    )
    results = agent.execute(task)
    assert len(results) == 2
    assert all(r.confidence == 0.0 for r in results)
    assert all(r.field_value in {"NOT_FOUND", "UNAVAILABLE"} for r in results)
    assert all(r.verification_status == "SOURCE_UNAVAILABLE" for r in results)


def test_fetch_exception_diagnostics_preservation():
    """F: Fetch exceptions preserve exception type and message in failure reason."""
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    from app.models.browser_session import BrowserSession
    from app.models.investigation import Investigation
    import json

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        inv = Investigation(status="created", input_data='{"name": "Wipro"}')
        db.add(inv)
        db.commit()
        db.refresh(inv)

        def failing_fetcher(url: str):
            raise ConnectionResetError("Connection refused by remote host")

        agent = BrowserResearchAgent(fetcher=failing_fetcher)
        task = make_task(preferred_sources=["company_website"], fallback_sources=[])

        with patch("app.db.session.SessionLocal", TestingSessionLocal):
            results = agent.execute(task, investigation_id=inv.id)

        session = db.query(BrowserSession).filter(BrowserSession.investigation_id == inv.id).first()
        assert session is not None
        assert session.status == "ERROR"
        meta = json.loads(session.failure_reason)
        assert "ConnectionResetError: Connection refused by remote host" in meta["failure_reason"]
    finally:
        db.close()


def test_successful_company_website_selected_as_evidence():
    """H: Reachable company website produces SUCCESS, PASSED, confidence=0.85, selected_as_evidence=true."""
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    from app.models.browser_session import BrowserSession
    from app.models.investigation import Investigation
    import json

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        inv = Investigation(status="created", input_data='{"name": "Wipro"}')
        db.add(inv)
        db.commit()
        db.refresh(inv)

        html = """
        <html>
          <head><title>Wipro | Consulting-Led and AI-Powered Technology Services</title></head>
          <body>
            <h1>Wipro Limited</h1>
            <p>Welcome to Wipro Limited official corporate portal in Karnataka, India.</p>
          </body>
        </html>
        """
        agent = BrowserResearchAgent(fetcher=lambda u: html)
        task = make_task(
            task_type="WEBSITE_VERIFICATION",
            target="https://www.wipro.com",
            preferred_sources=["company_website"],
            required_fields=["website_status", "page_title", "legal_name"],
        )

        with patch("app.db.session.SessionLocal", TestingSessionLocal):
            results = agent.execute(task, investigation_id=inv.id)

        assert len(results) == 3
        res_map = {r.field_name: r for r in results}
        assert res_map["website_status"].field_value == "AVAILABLE"
        assert res_map["website_status"].confidence == 0.85
        assert res_map["website_status"].verification_status == "VERIFIED"
        assert res_map["website_status"].source_url == "https://www.wipro.com"

        session = db.query(BrowserSession).filter(BrowserSession.investigation_id == inv.id).first()
        assert session is not None
        assert session.status == "SUCCESS"
        meta = json.loads(session.failure_reason)
        assert meta["relevance_result"] == "PASSED"
        assert meta["confidence"] == 0.85
        assert meta["selected_as_evidence"] is True
    finally:
        db.close()


def test_successful_third_party_directory_search_and_evidence_selection():
    """Verify that a real third-party registry (QuickCompany/Tofler/Zauba) produces SUCCESS, PASSED, non-zero confidence, and selected_as_evidence: true."""
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    from app.models.browser_session import BrowserSession
    from app.models.investigation import Investigation
    import json

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        inv = Investigation(status="created", input_data='{"name": "Wipro Limited"}')
        db.add(inv)
        db.commit()
        db.refresh(inv)

        search_html = """
        <html>
          <head><title>Search Results - QuickCompany</title></head>
          <body>
            <h1>Companies matching WIPRO LIMITED</h1>
            <div><a href="/company/WIPRO-LIMITED-in-Karnataka">WIPRO LIMITED - Karnataka</a></div>
          </body>
        </html>
        """
        profile_html = """
        <html>
          <head><title>WIPRO LIMITED - Company Registration & Financials</title></head>
          <body>
            <h1>WIPRO LIMITED</h1>
            <p>CIN: L32102KA1945PLC020800</p>
            <p>Company status: ACTIVE</p>
            <p>Address: Doddakannelli, Sarjapur Road, Bangalore, Karnataka 560035</p>
          </body>
        </html>
        """

        def mock_fetcher(url: str):
            if "wipro-limited-in-karnataka" in url.lower():
                return profile_html
            return search_html

        agent = BrowserResearchAgent(fetcher=mock_fetcher)
        task = make_task(
            task_type="THIRD_PARTY_RESEARCH",
            target="Wipro Limited",
            preferred_sources=["quickcompany.in"],
            required_fields=["legal_name", "company_status", "registered_address"],
        )

        with patch("app.db.session.SessionLocal", TestingSessionLocal):
            results = agent.execute(task, investigation_id=inv.id)

        assert len(results) == 3
        res_map = {r.field_name: r for r in results}
        assert res_map["legal_name"].field_value == "WIPRO LIMITED"
        assert res_map["company_status"].field_value == "ACTIVE"
        assert res_map["legal_name"].confidence == 0.80
        assert res_map["legal_name"].verification_status == "VERIFIED"

        session = db.query(BrowserSession).filter(BrowserSession.investigation_id == inv.id).first()
        assert session is not None
        assert session.status == "SUCCESS"
        meta = json.loads(session.failure_reason)
        assert meta["relevance_result"] == "PASSED"
        assert meta["confidence"] == 0.80
        assert meta["selected_as_evidence"] is True
    finally:
        db.close()


