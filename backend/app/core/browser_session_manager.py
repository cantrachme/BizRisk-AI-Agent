import uuid
import threading
import queue
import concurrent.futures
import os
from datetime import datetime, timezone
from typing import Optional
from playwright.sync_api import sync_playwright

class LiveBrowserSession:
    def __init__(self, investigation_id: uuid.UUID, task_id: str, source_name: str, timeout_seconds: int = 300):
        self.id = uuid.uuid4()
        self.investigation_id = investigation_id
        self.task_id = task_id
        self.source_name = source_name
        self.timeout_seconds = timeout_seconds
        self.created_at = datetime.now(timezone.utc)
        self.last_activity_at = datetime.now(timezone.utc)
        self.status = "CREATED"
        
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        
        # Block until initialization is successful on the worker thread
        future = concurrent.futures.Future()
        self._queue.put(("init", None, future))
        # Propagate initialization errors back to caller
        future.result(timeout=15)

    def touch(self):
        self.last_activity_at = datetime.now(timezone.utc)

    def is_expired(self) -> bool:
        elapsed = (datetime.now(timezone.utc) - self.last_activity_at).total_seconds()
        return elapsed > self.timeout_seconds

    def _run(self):
        playwright_context = None
        browser = None
        context = None
        page = None
        try:
            playwright_context = sync_playwright()
            playwright = playwright_context.__enter__()
            from app.core.config import get_settings
            headless = get_settings().playwright_headless
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(
                java_script_enabled=True,
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ignore_https_errors=True,
                viewport={"width": 1000, "height": 700},
            )
            page = context.new_page()
            self.status = "RUNNING"
            
            cmd, args, future = self._queue.get(timeout=15)
            if cmd == "init":
                future.set_result(True)
            else:
                self._queue.put((cmd, args, future))
        except Exception as e:
            self.status = "FAILED"
            try:
                cmd, args, future = self._queue.get(timeout=5)
                if cmd == "init":
                    future.set_exception(e)
            except Exception:
                pass
            if context:
                try: context.close()
                except Exception: pass
            if browser:
                try: browser.close()
                except Exception: pass
            if playwright_context:
                try: playwright_context.__exit__(None, None, None)
                except Exception: pass
            return

        while True:
            try:
                cmd, args, future = self._queue.get()
                if cmd == "init":
                    future.set_result(True)
                elif cmd == "goto":
                    url = args
                    try:
                        page.goto(url, wait_until="load", timeout=15000)
                        try:
                            screenshot_bytes = page.screenshot(type="png")
                            if screenshot_bytes and isinstance(screenshot_bytes, (bytes, bytearray)):
                                os.makedirs("/tmp/bizrisk_screenshots", exist_ok=True)
                                with open(f"/tmp/bizrisk_screenshots/{self.investigation_id}_{self.task_id}.png", "wb") as f:
                                    f.write(screenshot_bytes)
                        except Exception:
                            pass
                        future.set_result(True)
                    except Exception as e:
                        future.set_exception(e)
                elif cmd == "content":
                    try:
                        future.set_result(page.content())
                    except Exception as e:
                        future.set_exception(e)
                elif cmd == "url":
                    try:
                        future.set_result(page.url)
                    except Exception as e:
                        future.set_exception(e)
                elif cmd == "screenshot":
                    try:
                        screenshot_bytes = page.screenshot(type="png")
                        if screenshot_bytes and isinstance(screenshot_bytes, (bytes, bytearray)):
                            os.makedirs("/tmp/bizrisk_screenshots", exist_ok=True)
                            with open(f"/tmp/bizrisk_screenshots/{self.investigation_id}_{self.task_id}.png", "wb") as f:
                                f.write(screenshot_bytes)
                        future.set_result(screenshot_bytes)
                    except Exception as e:
                        future.set_exception(e)
                elif cmd == "click":
                    x, y = args
                    try:
                        page.evaluate(f"""() => {{
                            const el = document.elementFromPoint({x}, {y});
                            if (el) {{
                                el.focus();
                                if (el.tagName === 'LABEL' && el.htmlFor) {{
                                    const input = document.getElementById(el.htmlFor);
                                    if (input) input.focus();
                                }}
                            }}
                        }}""")
                        page.mouse.click(float(x), float(y))
                        future.set_result(True)
                    except Exception as e:
                        future.set_exception(e)
                elif cmd == "type":
                    text = args
                    try:
                        active_info_before = page.evaluate("""() => {
                            const el = document.activeElement;
                            if (!el) return { has_active: false };
                            return {
                                has_active: true,
                                tagName: el.tagName,
                                is_input: el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
                            };
                        }""")
                        if not active_info_before.get("is_input"):
                            raise ValueError("No active input element focused in the browser page. Please click the input field first.")
                        
                        page.keyboard.type(str(text))
                        
                        active_info_after = page.evaluate("""() => {
                            const el = document.activeElement;
                            if (!el) return { has_active: false };
                            return {
                                has_active: true,
                                tagName: el.tagName,
                                value: el.value || '',
                                is_input: el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
                            };
                        }""")
                        if not active_info_after.get("value"):
                            raise ValueError("Input field remained empty after typing")
                        future.set_result(True)
                    except Exception as e:
                        future.set_exception(e)
                elif cmd == "clear":
                    try:
                        active_info_before = page.evaluate("""() => {
                            const el = document.activeElement;
                            if (!el) return { has_active: false };
                            return {
                                has_active: true,
                                tagName: el.tagName,
                                is_input: el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
                            };
                        }""")
                        if not active_info_before.get("is_input"):
                            raise ValueError("No active input element focused in the browser page. Please click the input field first.")
                        
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Meta+A")
                        page.keyboard.press("Backspace")
                        
                        active_info_after = page.evaluate("""() => {
                            const el = document.activeElement;
                            if (!el) return { has_active: false };
                            return {
                                has_active: true,
                                tagName: el.tagName,
                                value: el.value || '',
                                is_input: el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable
                            };
                        }""")
                        if active_info_after.get("value") != "":
                            raise ValueError("Input field could not be cleared")
                        future.set_result(True)
                    except Exception as e:
                        future.set_exception(e)
                elif cmd == "close":
                    try:
                        if page and not getattr(page, "is_closed", lambda: False)():
                            screenshot_bytes = page.screenshot(type="png")
                            if screenshot_bytes and isinstance(screenshot_bytes, (bytes, bytearray)):
                                os.makedirs("/tmp/bizrisk_screenshots", exist_ok=True)
                                with open(f"/tmp/bizrisk_screenshots/{self.investigation_id}_{self.task_id}.png", "wb") as f:
                                    f.write(screenshot_bytes)
                    except Exception:
                        pass
                    try:
                        page.close()
                    except Exception:
                        pass
                    try:
                        context.close()
                    except Exception:
                        pass
                    try:
                        browser.close()
                    except Exception:
                        pass
                    try:
                        playwright_context.__exit__(None, None, None)
                    except Exception:
                        pass
                    self.status = "COMPLETED"
                    future.set_result(True)
                    break
            except Exception as e:
                self.status = "FAILED"
                break

    def goto(self, url: str) -> bool:
        self.touch()
        future = concurrent.futures.Future()
        self._queue.put(("goto", url, future))
        return future.result(timeout=20)

    def content(self) -> str:
        self.touch()
        future = concurrent.futures.Future()
        self._queue.put(("content", None, future))
        return future.result(timeout=10)

    def get_url(self) -> str:
        self.touch()
        future = concurrent.futures.Future()
        self._queue.put(("url", None, future))
        return future.result(timeout=10)

    def screenshot(self) -> bytes:
        self.touch()
        future = concurrent.futures.Future()
        self._queue.put(("screenshot", None, future))
        return future.result(timeout=10)

    def click(self, x: float, y: float) -> bool:
        self.touch()
        future = concurrent.futures.Future()
        self._queue.put(("click", (x, y), future))
        return future.result(timeout=10)

    def type(self, text: str) -> bool:
        self.touch()
        future = concurrent.futures.Future()
        self._queue.put(("type", text, future))
        return future.result(timeout=10)

    def clear(self) -> bool:
        self.touch()
        future = concurrent.futures.Future()
        self._queue.put(("clear", None, future))
        return future.result(timeout=10)

    def close(self):
        if self.status in {"COMPLETED", "FAILED"}:
            return
        future = concurrent.futures.Future()
        self._queue.put(("close", None, future))
        try:
            future.result(timeout=10)
        except Exception:
            pass

class BrowserSessionManager:
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    def start_session(self, investigation_id: uuid.UUID, task_id: str, source_name: str, timeout_seconds: int = 300) -> LiveBrowserSession:
        key = (str(investigation_id), task_id, source_name)
        with self._lock:
            if key in self._sessions:
                try:
                    self._sessions[key].close()
                except Exception:
                    pass
            session = LiveBrowserSession(investigation_id, task_id, source_name, timeout_seconds)
            self._sessions[key] = session
            return session

    def get_session(self, investigation_id: uuid.UUID, task_id: str, source_name: Optional[str] = None) -> Optional[LiveBrowserSession]:
        with self._lock:
            if source_name:
                key = (str(investigation_id), task_id, source_name)
                session = self._sessions.get(key)
                if session:
                    if session.is_expired():
                        try:
                            session.close()
                        except Exception:
                            pass
                        self._sessions.pop(key, None)
                        return None
                    session.touch()
                return session
            else:
                # API lookup without source: search for the active session currently waiting for human input or recently active
                active_session = None
                for key, session in list(self._sessions.items()):
                    if key[0] == str(investigation_id) and key[1] == task_id:
                        if session.is_expired():
                            try:
                                session.close()
                            except Exception:
                                pass
                            self._sessions.pop(key, None)
                        else:
                            # Prioritize running/waiting session
                            session.touch()
                            active_session = session
                            break
                return active_session

    def close_session(self, investigation_id: uuid.UUID, task_id: str, source_name: Optional[str] = None):
        with self._lock:
            if source_name:
                key = (str(investigation_id), task_id, source_name)
                session = self._sessions.pop(key, None)
                if session:
                    try:
                        session.close()
                    except Exception:
                        pass
            else:
                for key, session in list(self._sessions.items()):
                    if key[0] == str(investigation_id) and key[1] == task_id:
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
                session = self._sessions.pop(key, None)
                if session:
                    try:
                        session.close()
                    except Exception:
                        pass

browser_session_manager = BrowserSessionManager()
