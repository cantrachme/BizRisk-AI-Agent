import uuid
import threading
import os
from datetime import datetime, timezone
from typing import Optional
from playwright.sync_api import sync_playwright

class LiveBrowserSession:
    def __init__(self, investigation_id: uuid.UUID, task_id: str, timeout_seconds: int = 300):
        self.id = uuid.uuid4()
        self.investigation_id = investigation_id
        self.task_id = task_id
        self.timeout_seconds = timeout_seconds
        self.created_at = datetime.now(timezone.utc)
        self.last_activity_at = datetime.now(timezone.utc)
        self.status = "CREATED"
        
        self.playwright_context = sync_playwright()
        self.playwright = self.playwright_context.__enter__()
        
        headless = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
        self.browser = self.playwright.chromium.launch(headless=headless)
        self.context = self.browser.new_context(
            java_script_enabled=True,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            ignore_https_errors=True,
            viewport={"width": 1000, "height": 700},
        )
        self.page = self.context.new_page()
        self.status = "RUNNING"

    def touch(self):
        self.last_activity_at = datetime.now(timezone.utc)

    def is_expired(self) -> bool:
        elapsed = (datetime.now(timezone.utc) - self.last_activity_at).total_seconds()
        return elapsed > self.timeout_seconds

    def close(self):
        try:
            self.page.close()
        except Exception:
            pass
        try:
            self.context.close()
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self.playwright_context.__exit__(None, None, None)
        except Exception:
            pass
        self.status = "COMPLETED"

class BrowserSessionManager:
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    def start_session(self, investigation_id: uuid.UUID, task_id: str, timeout_seconds: int = 300) -> LiveBrowserSession:
        key = (str(investigation_id), task_id)
        with self._lock:
            if key in self._sessions:
                try:
                    self._sessions[key].close()
                except Exception:
                    pass
            session = LiveBrowserSession(investigation_id, task_id, timeout_seconds)
            self._sessions[key] = session
            return session

    def get_session(self, investigation_id: uuid.UUID, task_id: str) -> Optional[LiveBrowserSession]:
        key = (str(investigation_id), task_id)
        with self._lock:
            session = self._sessions.get(key)
            if session:
                if session.is_expired():
                    try:
                        session.close()
                    except Exception:
                        pass
                    del self._sessions[key]
                    return None
                session.touch()
            return session

    def close_session(self, investigation_id: uuid.UUID, task_id: str):
        key = (str(investigation_id), task_id)
        with self._lock:
            session = self._sessions.pop(key, None)
            if session:
                try:
                    session.close()
                except Exception:
                    pass

    def cleanup_expired_sessions(self):
        with self._lock:
            expired_keys = []
            for key, session in self._sessions.items():
                if session.is_expired():
                    expired_keys.append(key)
            for key in expired_keys:
                session = self._sessions.pop(key)
                try:
                    session.close()
                except Exception:
                    pass

browser_session_manager = BrowserSessionManager()
