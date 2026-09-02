import pytest
import uuid
import json
import time
import threading
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from app.core.browser_session_manager import browser_session_manager, LiveBrowserSession
from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask as GraphTask
from app.core.exceptions import HumanInterventionRequiredException

@pytest.fixture(autouse=True)
def cleanup_sessions():
    browser_session_manager._sessions.clear()
    yield
    # Safely close all sessions after test
    with browser_session_manager._lock:
        for session in list(browser_session_manager._sessions.values()):
            try:
                session.close()
            except Exception:
                pass
    browser_session_manager._sessions.clear()


# TEST 1 & 2 & 3: CAPTCHA detected records blocked attempt with confidence 0
def test_live_session_captcha_kept_alive():
    from app.db.session import SessionLocal, db_lock
    from app.models.investigation import Investigation
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-1"

    with db_lock:
        with SessionLocal() as db:
            inv = Investigation(
                id=inv_id,
                user_id="test-user",
                status="IN_PROGRESS",
                input_data='{"business_name": "Test"}',
                created_at=datetime.now(timezone.utc),
            )
            db.add(inv)
            db.commit()
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        # Configure mock page content to return a CAPTCHA challenge
        mock_page.content.return_value = "<html><title>Please Solve Captcha</title></html>"
        mock_page.url = "https://services.gst.gov.in/services/searchtp"
        mock_page.screenshot.return_value = b"\x89PNG\r\n\x1a\nCAPTCHA_SCREENSHOT"
        
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
            fallback_sources=[],
        )
        
        results = agent.execute(task, investigation_id=inv_id)
        assert len(results) == 1
        assert results[0].confidence == 0.0
        assert results[0].field_value == "NOT_FOUND"


# TEST 4 & 5: Human completion resumes the SAME session and preserves page URL state
def test_live_session_resumes_same_page_instance():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-2"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        mock_page.screenshot.return_value = b"\x89PNG\r\n\x1a\nCAPTCHA_SCREENSHOT"
        
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
            
        session1 = browser_session_manager.get_session(inv_id, task_id, "gst.gov.in")
        assert session1 is not None
        
        # 2. Mock page content changes
        mock_page.content.return_value = "<html><title>Wipro Limited</title><body>Wipro Limited is Active.</body></html>"
        mock_page.url = "https://services.gst.gov.in/services/profile"
        
        # Execute again
        results = agent.execute(task, investigation_id=inv_id)
        
        legal_name_res = next(r for r in results if r.field_name == "legal_name")
        assert legal_name_res.field_value == "Wipro Limited"
        
        # Page was NOT navigated back (goto was not called again on resume)
        assert mock_page.goto.call_count == 1 
        
        # Session is closed upon task completion
        assert browser_session_manager.get_session(inv_id, task_id, "gst.gov.in") is None


# TEST 7: Session timeout closes browser context
def test_live_session_timeout_expiration():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-3"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in", timeout_seconds=1)
        assert session is not None
        
        # Wait for expiration
        time.sleep(1.2)
        
        expired_session = browser_session_manager.get_session(inv_id, task_id, "gst.gov.in")
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
        session = browser_session_manager.start_session(inv_id_1, task_id, "gst.gov.in")
        assert session is not None
        
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
        
        session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in")
        
        from app.api.investigations import post_task_click, post_task_type
        
        # Click action
        click_res = post_task_click(str(inv_id), task_id, {"x": 120, "y": 450})
        assert click_res["status"] == "success"
        mock_page.mouse.click.assert_called_once_with(120.0, 450.0)
        
        # Type action
        type_res = post_task_type(str(inv_id), task_id, {"text": "hello-world"})
        assert type_res["status"] == "success"
        mock_page.keyboard.type.assert_called_once_with("hello-world")


# TEST 10: Multi-threaded request execution safety (FastAPI concurrency model simulation)
def test_multithreaded_request_handling_safe():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-6"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        # screenshot returns a generic byte sequence
        mock_page.screenshot.return_value = b"MOCK-PNG-BYTES"
        
        session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in")
        
        # We will trigger commands from 4 distinct threads concurrently
        def execute_screenshot():
            return session.screenshot()
            
        def execute_click():
            return session.click(150.0, 200.0)
            
        def execute_type():
            return session.type("multithreaded text input")

        with ThreadPoolExecutor(max_workers=4) as executor:
            fut1 = executor.submit(execute_screenshot)
            fut2 = executor.submit(execute_click)
            fut3 = executor.submit(execute_type)
            fut4 = executor.submit(execute_screenshot)
            
            # None of these calls should raise Playwright thread-affinity exceptions
            assert fut1.result() == b"MOCK-PNG-BYTES"
            assert fut2.result() is True
            assert fut3.result() is True
            assert fut4.result() == b"MOCK-PNG-BYTES"
            
        mock_page.screenshot.call_count == 2
        mock_page.mouse.click.assert_called_once_with(150.0, 200.0)
        mock_page.keyboard.type.assert_called_once_with("multithreaded text input")


# TEST 11: Fallback candidate sessions do not overwrite or close active preferred-source HITL session
def test_fallback_does_not_overwrite_active_hitl_session():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-7"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        # Create active preferred source session
        preferred_session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in")
        assert preferred_session is not None
        preferred_session.status = "RUNNING"
        
        # Simulate fallback traversal starting a session for "third_party"
        fallback_session = browser_session_manager.start_session(inv_id, task_id, "third_party")
        assert fallback_session is not None
        fallback_session.status = "RUNNING"
        
        # Check that both sessions coexist in the manager under different keys
        assert browser_session_manager.get_session(inv_id, task_id, "gst.gov.in") is not None
        assert browser_session_manager.get_session(inv_id, task_id, "third_party") is not None
        
        # API session retrieval (without source parameter) retrieves the active session
        resolved_session = browser_session_manager.get_session(inv_id, task_id)
        assert resolved_session is not None
        # Should resolve the primary active session
        assert resolved_session.source_name == "gst.gov.in"


# TEST 12: Regression test for CAPTCHA typing validation
def test_captcha_input_typing_validation_regression():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-8"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        def mock_evaluate(script, *args):
            if "is_input" in script:
                if "value" in script:
                    return {
                        "has_active": True,
                        "tagName": "INPUT",
                        "value": "12345",
                        "is_input": True
                    }
                return {
                    "has_active": True,
                    "tagName": "INPUT",
                    "is_input": True
                }
            return None
        
        mock_page.evaluate.side_effect = mock_evaluate
        mock_page.screenshot.return_value = b"MOCK-CAPTCHA-SCREENSHOT-PNG"
        
        session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in")
        assert session is not None
        
        click_success = session.click(200.0, 350.0)
        assert click_success is True
        mock_page.mouse.click.assert_called_once_with(200.0, 350.0)
        
        type_success = session.type("12345")
        assert type_success is True
        mock_page.keyboard.type.assert_called_once_with("12345")
        
        screenshot_bytes = session.screenshot()
        assert screenshot_bytes == b"MOCK-CAPTCHA-SCREENSHOT-PNG"
        assert mock_page.screenshot.call_count == 1


# TEST 13: Regression test for CAPTCHA typing validation failure mode (empty value)
def test_captcha_input_typing_validation_failure():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-9"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        def mock_evaluate(script, *args):
            if "is_input" in script:
                if "value" in script:
                    return {
                        "has_active": True,
                        "tagName": "INPUT",
                        "value": "",  # Empty value triggers error!
                        "is_input": True
                    }
                return {
                    "has_active": True,
                    "tagName": "INPUT",
                    "is_input": True
                }
            return None
        
        mock_page.evaluate.side_effect = mock_evaluate
        
        session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in")
        assert session is not None
        
        session.click(200.0, 350.0)
        
        with pytest.raises(ValueError) as excinfo:
            session.type("12345")
        assert "Input field remained empty after typing" in str(excinfo.value)


# TEST 14: Regression test for input clearing action
def test_live_session_clear_action():
    inv_id = uuid.uuid4()
    task_id = "TASK-LIVE-10"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        
        def mock_evaluate(script, *args):
            if "is_input" in script:
                if "value" in script:
                    return {
                        "has_active": True,
                        "tagName": "INPUT",
                        "value": "",
                        "is_input": True
                    }
                return {
                    "has_active": True,
                    "tagName": "INPUT",
                    "is_input": True
                }
            return None
        
        mock_page.evaluate.side_effect = mock_evaluate
        
        session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in")
        assert session is not None
        
        from app.api.investigations import post_task_clear
        clear_res = post_task_clear(str(inv_id), task_id)
        assert clear_res["status"] == "success"
        
        mock_page.keyboard.press.assert_has_calls([
            mock.call("Control+A"),
            mock.call("Meta+A"),
            mock.call("Backspace")
        ])


def test_get_task_screenshot_active_and_persisted_disk_fallback():
    inv_id = uuid.uuid4()
    task_id = "TASK-SCREENSHOT-TEST"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        mock_page.screenshot.return_value = b"\x89PNG\r\n\x1a\nREAL_SCREENSHOT_BYTES"

        from app.api.investigations import get_task_screenshot
        
        # 1. Start active session and capture screenshot
        session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in")
        assert session is not None
        
        resp = get_task_screenshot(str(inv_id), task_id)
        assert resp.body == b"\x89PNG\r\n\x1a\nREAL_SCREENSHOT_BYTES"
        assert resp.media_type == "image/png"
        
        # 2. Close active session and verify disk fallback retrieves saved file
        browser_session_manager.close_session(inv_id, task_id)
        
        resp_disk = get_task_screenshot(str(inv_id), task_id)
        assert resp_disk.body == b"\x89PNG\r\n\x1a\nREAL_SCREENSHOT_BYTES"
        assert resp_disk.media_type == "image/png"


def test_screenshot_retrieval_lifecycle_comprehensive():
    import os
    import pytest
    from fastapi import HTTPException
    from app.api.investigations import get_task_screenshot
    from app.core.browser_session_manager import BrowserSessionManager

    inv_id = uuid.uuid4()
    task_id = "TASK-LIFECYCLE-1"
    
    with mock.patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw = mock_sync_pw.return_value.__enter__.return_value
        mock_browser = mock_pw.chromium.launch.return_value
        mock_context = mock_browser.new_context.return_value
        mock_page = mock_context.new_page.return_value
        mock_page.screenshot.return_value = b"\x89PNG\r\n\x1a\nATTEMPT_1_SCREENSHOT"

        # A. Active session screenshot retrieval
        session = browser_session_manager.start_session(inv_id, task_id, "gst.gov.in")
        assert session is not None
        
        resp_active = get_task_screenshot(str(inv_id), task_id)
        assert resp_active.body == b"\x89PNG\r\n\x1a\nATTEMPT_1_SCREENSHOT"

        # B. Multiple attempts: attempt 2 updates the latest screenshot
        mock_page.screenshot.return_value = b"\x89PNG\r\n\x1a\nATTEMPT_2_LATEST_SCREENSHOT"
        session.screenshot()
        
        resp_updated = get_task_screenshot(str(inv_id), task_id)
        assert resp_updated.body == b"\x89PNG\r\n\x1a\nATTEMPT_2_LATEST_SCREENSHOT"

        # C. Completed session cleanup -> close session
        browser_session_manager.close_session(inv_id, task_id)
        assert browser_session_manager.get_session(inv_id, task_id) is None
        
        resp_completed = get_task_screenshot(str(inv_id), task_id)
        assert resp_completed.body == b"\x89PNG\r\n\x1a\nATTEMPT_2_LATEST_SCREENSHOT"

        # D. Clear BrowserSessionManager in-memory dictionary completely (simulate process restart)
        browser_session_manager._sessions.clear()
        assert len(browser_session_manager._sessions) == 0

        resp_restart = get_task_screenshot(str(inv_id), task_id)
        assert resp_restart.body == b"\x89PNG\r\n\x1a\nATTEMPT_2_LATEST_SCREENSHOT"

        # E. Missing screenshot returns 404
        non_existent_id = uuid.uuid4()
        with pytest.raises(HTTPException) as exc_info:
            get_task_screenshot(str(non_existent_id), "TASK-NON-EXISTENT")
        assert exc_info.value.status_code == 404
        assert "No active browser session or saved screenshot found" in exc_info.value.detail



