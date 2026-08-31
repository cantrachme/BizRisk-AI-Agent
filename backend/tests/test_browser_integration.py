import pytest
from unittest import mock
from app.agents.browser import BrowserResearchAgent, detect_human_intervention
from app.graph.state import ResearchTask
from app.core.exceptions import HumanInterventionRequiredException
from app.models.source_registry import SourceRegistry
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base


def make_task(
    task_type="GST_VERIFICATION",
    target="27ABCDE1234F1Z5",
    preferred_sources=None,
    fallback_sources=None,
    required_fields=None,
    allowed_domains=None,
):
    task = ResearchTask(
        task_id="TASK-INT-001",
        task_type=task_type,
        target=target,
        objective="Integration test task",
        required_fields=required_fields or ["legal_name", "gst_status"],
        priority=1,
        preferred_sources=preferred_sources or ["gst.gov.in"],
        fallback_sources=fallback_sources or ["third_party"],
    )
    if allowed_domains is not None:
        task.allowed_domains = allowed_domains
    return task


# 1. Test CAPTCHA detection and exception raising
def test_browser_captcha_trigger():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><title>Verification</title><body>recaptcha box here. please solve the captcha</body></html>")
    task = make_task()
    with pytest.raises(HumanInterventionRequiredException) as excinfo:
        agent.execute(task)
    assert excinfo.value.intervention_type == "CAPTCHA"


# 2. Test OTP detection and exception raising
def test_browser_otp_trigger():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><body>verification code sent to your mobile. enter otp to continue.</body></html>")
    task = make_task()
    with pytest.raises(HumanInterventionRequiredException) as excinfo:
        agent.execute(task)
    assert excinfo.value.intervention_type == "OTP"


# 3. Test Login requirement detection and exception raising
def test_browser_login_trigger():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><body>member login page. sign in to proceed.</body></html>")
    task = make_task()
    with pytest.raises(HumanInterventionRequiredException) as excinfo:
        agent.execute(task)
    assert excinfo.value.intervention_type == "LOGIN_REQUIRED"


# 4. Test Empty / Fetch failure gracefully falls back
def test_browser_fetch_failure_fallback():
    def failing_fetcher(url):
        raise TimeoutError("Connection timed out")
    
    agent = BrowserResearchAgent(fetcher=failing_fetcher)
    task = make_task(required_fields=["legal_name", "gst_status"])
    results = agent.execute(task)
    
    assert len(results) == 2
    # When page fetch fails, legal_name defaults to NOT_FOUND and gst_status defaults to UNAVAILABLE
    assert results[0].field_name == "legal_name"
    assert results[0].field_value == "NOT_FOUND"
    assert results[1].field_name == "gst_status"
    assert results[1].field_value == "UNAVAILABLE"


# 5. Test Allowed Domains Restrictions
def test_browser_allowed_domains_restriction():
    agent = BrowserResearchAgent(fetcher=lambda url: "<html><body>Content</body></html>")
    # Selected source is 'gst.gov.in', but allowed_domains only contains 'mca.gov.in'
    task = make_task(preferred_sources=["gst.gov.in"], allowed_domains=["mca.gov.in"])
    results = agent.execute(task)
    assert results == []


# 6. Test Source Registry database resolution
def test_browser_source_registry_resolution():
    # Setup in-memory sqlite to test DB registry query integration
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db_session = Session()

    # Create dummy source registry entry
    db_source = SourceRegistry(
        name="Custom Registry GST",
        domain="custom-gst.gov.in",
        type="GST_VERIFICATION",
        enabled=True,
        config_json='{"confidence": 0.99}'
    )
    db_session.add(db_source)
    db_session.commit()

    agent = BrowserResearchAgent(fetcher=lambda url: "<html><body>Content</body></html>")
    task = make_task(preferred_sources=["Custom Registry GST"])

    # Mock SessionLocal and get_source_by_name helper
    from app.services.source_registry import get_source_by_name
    
    with mock.patch("app.db.session.SessionLocal", return_value=db_session), \
         mock.patch("app.services.source_registry.get_source_by_name", return_value=db_source):
        results = agent.execute(task)

    assert len(results) == 2
    assert results[0].source_name == "Custom Registry GST"
    assert results[0].confidence == 0.99
    db_session.close()

def test_real_browser_fetcher_returns_html():
    from app.agents.browser import BrowserResearchAgent
    agent=BrowserResearchAgent()
    html=agent.fetcher("https://example.com")
    assert "<html" in html.lower()
