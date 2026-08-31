import pytest
import uuid
import json
import time
from unittest import mock
from datetime import datetime, timezone, timedelta

from app.core.browser_session_manager import browser_session_manager, LiveBrowserSession
from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask as GraphTask
from app.core.exceptions import HumanInterventionRequiredException

@pytest.fixture(autouse=True)
def cleanup_sessions():
    # Make sure we clean up in-memory sessions between runs
    browser_session_manager._sessions.clear()
    yield
    browser_session_manager._sessions.clear()


# TEST 1 & 2 & 3: CAPTCHA detected keeps the Playwright session alive and sets correct states
def test_live_session_captcha_kept_alive():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-1"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        # Configure mock page content to return a CAPTCHA challenge
        mock_page.content.return_value = "<html><title>Please Solve Captcha</title></html>"
        mock_page.url = "https://services.gst.gov.in/services/searchtp"
        
        agent = BrowserResearchAgent()
        # Force use_live_session evaluation to True
        agent.fetcher = agent._fetch_page
        
        task = GraphTask(
            task_id=task_id,
            task_type="GST_VERIFICATION",
            target="27AAACW0387R1Z6",
            objective="Verify GSTIN",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=["gst.gov.in"],
        )
        
        # Executing the task throws the exception but does not close the browser
        with pytest.raises(HumanInterventionRequiredException) as ex:
            agent.execute(task, investigation_id=inv_id)
            
        assert ex.value.intervention_type == "CAPTCHA"
        
        # The session is registered and still RUNNING / active
        session = browser_session_manager.get_session(inv_id, task_id)
        assert session is not None
        assert session.status == "RUNNING"
        # The browser close, context close, page close were NEVER called!
        assert mock_browser.close.call_count == 0
        assert mock_page.close.call_count == 0


# TEST 4 & 5: Human completion resumes the SAME session and preserves page URL state
def test_live_session_resumes_same_page_instance():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-2"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        # 1. First run hits captcha
        mock_page.content.return_value = "<html><title>CAPTCHA challenge</title></html>"
        mock_page.url = "https://services.gst.gov.in/services/searchtp"
        
        agent = BrowserResearchAgent()
        agent.fetcher = agent._fetch_page
        
        task = GraphTask(
            task_id=task_id,
            task_type="GST_VERIFICATION",
            target="27AAACW0387R1Z6",
            objective="Verify GSTIN",
            required_fields=["legal_name"],
            priority=1,
            preferred_sources=["gst.gov.in"],
        )
        
        try:
            agent.execute(task, investigation_id=inv_id)
        except HumanInterventionRequiredException:
            pass
            
        # Verify first session was created
        session1 = browser_session_manager.get_session(inv_id, task_id)
        assert session1 is not None
        
        # 2. Mock page content changes (user solved captcha manually)
        mock_page.content.return_value = "<html><title>Wipro Limited</title><body>Wipro Limited is Active.</body></html>"
        mock_page.url = "https://services.gst.gov.in/services/profile"
        
        # Execute again (simulate human resume)
        results = agent.execute(task, investigation_id=inv_id)
        
        # Page content is processed, evidence extracted
        legal_name_res = next(r for r in results if r.field_name == "legal_name")
        assert legal_name_res.field_value == "Wipro Limited"
        
        # Page was NOT navigated back to search page (goto was not called again)
        # It was called exactly once in the first session creation, but not on resume
        assert mock_page.goto.call_count == 1 
        
        # The session is closed upon completion
        assert browser_session_manager.get_session(inv_id, task_id) is None


# TEST 7: Session timeout closes browser context
def test_live_session_timeout_expiration():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-3"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        # Create session with 1 second expiry
        session = browser_session_manager.start_session(inv_id, task_id, timeout_seconds=1)
        assert session is not None
        
        # Wait for expiration
        time.sleep(1.2)
        
        # Check retrieval returns None and closes Playwright objects
        expired_session = browser_session_manager.get_session(inv_id, task_id)
        assert expired_session is None
        
        # context.close() and browser.close() must be called
        assert mock_context.close.call_count == 1
        assert mock_browser.close.call_count == 1


# TEST 9: Wrong investigation cannot access session (Security Isolation)
def test_session_isolation_security():
    inv_id_1 = uuid.uuid4()
    inv_id_2 = uuid.uuid4()
    task_id = "TASK-LIVE-4"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        session = browser_session_manager.start_session(inv_id_1, task_id)
        assert session is not None
        
        # Attempt retrieve using second investigation ID yields None
        assert browser_session_manager.get_session(inv_id_2, task_id) is None


# TEST: Frontend interactive mouse clicks and keyboard typing coordination
def test_session_interaction_endpoints():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-5"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        session = browser_session_manager.start_session(inv_id, task_id)
        
        # Mouse click coordinates are successfully forwarded to playwright page
        session.page.mouse.click = mock.Mock()
        session.page.keyboard.type = mock.Mock()
        
        from app.api.investigations import post_task_click, post_task_type
        
        # Click action
        click_res = post_task_click(str(inv_id), task_id, {"x": 120, "y": 450})
        assert click_res["status"] == "success"
        session.page.mouse.click.assert_called_once_with(120.0, 450.0)
        
        # Type action
        type_res = post_task_type(str(inv_id), task_id, {"text": "hello-world"})
        assert type_res["status"] == "success"
        session.page.keyboard.type.assert_called_once_with("hello-world")
