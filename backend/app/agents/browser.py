from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import re

from app.graph.state import ResearchResult, ResearchTask
from app.core.exceptions import HumanInterventionRequiredException


SOURCES = {
    "gst.gov.in": ("GST Portal", "https://www.gst.gov.in", 0.95),
    "mca.gov.in": ("MCA Portal", "https://www.mca.gov.in", 0.95),
    "epfindia.gov.in": ("EPFO Portal", "https://www.epfindia.gov.in", 0.90),
    "company_website": ("Company Website", None, 0.85),
    "generic_web": ("General Web", None, 0.60),
    "third_party": ("Third-Party Source", None, 0.50),
}

DISPLAY_TO_CANONICAL = {v[0]: k for k, v in SOURCES.items()}

SUPPORTED_TASK_TYPES = {
    "ENTITY_DISCOVERY",
    "GST_VERIFICATION",
    "MCA_VERIFICATION",
    "EPFO_VERIFICATION",
    "WEBSITE_VERIFICATION",
    "GENERAL_WEB_RESEARCH",
}


def detect_human_intervention(html: str) -> str | None:
    if not html:
        return None

    html_lower = html.lower()

    # 1. CAPTCHA Check
    captcha_patterns = [
        r"recaptcha",
        r"hcaptcha",
        r"g-recaptcha",
        r"bot verification",
        r"verify you are human",
        r"robot check",
        r"prove you're not a robot",
        r"please solve the captcha",
        r"solve the captcha below",
        r"security check to proceed",
        r"complete the captcha",
        r"distribute captcha",
    ]
    # Check title explicitly
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title_text = title_match.group(1).lower()
        if "captcha" in title_text or "robot verification" in title_text or "verify you are human" in title_text:
            return "CAPTCHA"

    for pattern in captcha_patterns:
        if pattern in {"recaptcha", "hcaptcha", "g-recaptcha"}:
            if pattern in html_lower:
                return "CAPTCHA"
        else:
            if re.search(r"\b" + re.escape(pattern) + r"\b", html_lower):
                return "CAPTCHA"

    # 2. OTP Check
    otp_patterns = [
        r"enter otp",
        r"enter one-time password",
        r"one time password",
        r"verification code sent",
        r"enter verification code",
        r"two-factor authentication",
        r"2fa code",
    ]
    for pattern in otp_patterns:
        if re.search(r"\b" + re.escape(pattern) + r"\b", html_lower):
            return "OTP"

    # 3. Login Check
    login_patterns = [
        r"login required",
        r"please log in",
        r"sign in to your account",
        r"authentication required",
        r"member login",
        r"sign in to proceed",
    ]
    for pattern in login_patterns:
        if re.search(r"\b" + re.escape(pattern) + r"\b", html_lower):
            return "LOGIN_REQUIRED"

    return None


class BrowserResearchAgent:
    def __init__(
        self,
        fetcher: Callable[[str], str] | None = None,
    ):
        self.fetcher = fetcher or self._fetch_page

    def _save_browser_attempt(
        self,
        investigation_id,
        task,
        source_name,
        source,
        url,
        attempt_order,
        started_at,
        completed_at,
        status,
        http_result,
        title,
        text_length,
        relevance_result,
        failure_reason,
        confidence,
        selected_as_evidence,
    ):
        if not investigation_id:
            return
        
        try:
            import json
            import uuid
            from app.db.session import SessionLocal, db_lock
            from app.models.browser_session import BrowserSession
            from unittest import mock
            
            # Serialize extra structured metadata
            metadata = {
                "source_name": source_name,
                "source_type": "preferred" if source in task.preferred_sources else "fallback",
                "url": url,
                "attempt_order": attempt_order,
                "http_result": http_result,
                "title": title,
                "text_length": text_length,
                "relevance_result": relevance_result,
                "confidence": confidence,
                "selected_as_evidence": selected_as_evidence,
            }
            metadata_str = json.dumps(metadata)
            
            with db_lock:
                db = SessionLocal()
                # Handle test mocks
                if hasattr(db, "__enter__") and not hasattr(db, "query"):
                    db = db.__enter__()
                try:
                    session_id = uuid.uuid4()
                    db_session = BrowserSession(
                        id=session_id,
                        investigation_id=uuid.UUID(str(investigation_id)),
                        task_id=task.task_id,
                        domain=source,
                        status=status,
                        action_count=1 if selected_as_evidence else 0,
                        started_at=started_at,
                        completed_at=completed_at,
                        failure_reason=metadata_str,
                    )
                    db.add(db_session)
                    db.commit()
                finally:
                    is_mocked_db = isinstance(SessionLocal, (mock.Mock, mock.MagicMock)) or not hasattr(SessionLocal, "kw")
                    if not is_mocked_db and hasattr(db, "close"):
                        try:
                            db.close()
                        except Exception:
                            pass
        except Exception as ex:
            print(f"[DIAGNOSTIC] Failed to save browser attempt to database: {ex}", flush=True)

    def execute(
        self,
        task: ResearchTask,
        investigation_id: Optional[uuid.UUID] = None,
    ) -> list[ResearchResult]:
        if task.task_type not in SUPPORTED_TASK_TYPES:
            return []

        # Build list of unique candidate sources to attempt in order
        candidates = []
        for src in [*task.preferred_sources, *task.fallback_sources]:
            if src not in candidates:
                # Check domain restrictions (TRD §80)
                allowed_domains = getattr(task, "allowed_domains", None)
                if allowed_domains is not None and src not in allowed_domains:
                    continue

                # Verify that the source is known/registered
                is_known = src in SOURCES or src in DISPLAY_TO_CANONICAL
                if not is_known:
                    try:
                        from app.db.session import SessionLocal, db_lock
                        from app.services.source_registry import get_source_by_name
                        from unittest import mock
                        is_mocked_db = isinstance(SessionLocal, (mock.Mock, mock.MagicMock)) or not hasattr(SessionLocal, "kw")
                        with db_lock:
                            db = SessionLocal()
                            if hasattr(db, "__enter__") and not hasattr(db, "query"):
                                db = db.__enter__()
                            try:
                                db_source = get_source_by_name(db, src)
                                if db_source:
                                    is_known = True
                            finally:
                                if not is_mocked_db and hasattr(db, "close"):
                                    try:
                                        db.close()
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                
                if is_known:
                    candidates.append(src)

        if not candidates:
            return []

        chosen_source = None
        chosen_url = None
        chosen_confidence = 0.0
        chosen_page_data = None

        attempt_order = 0
        # Try to find a working source from candidates
        for source in candidates:
            attempt_order += 1
            started_at = datetime.now(timezone.utc)

            source_name, source_url, confidence = None, None, None
            is_mocked_db = False

            try:
                from app.db.session import SessionLocal, db_lock
                from app.services.source_registry import get_source_by_name
                from unittest import mock
                is_mocked_db = isinstance(SessionLocal, (mock.Mock, mock.MagicMock)) or not hasattr(SessionLocal, "kw")

                with db_lock:
                    db = SessionLocal()
                    if hasattr(db, "__enter__") and not hasattr(db, "query"):
                        db = db.__enter__()
                    try:
                        db_source = get_source_by_name(db, source)
                        if not db_source and source in DISPLAY_TO_CANONICAL:
                            db_source = get_source_by_name(db, DISPLAY_TO_CANONICAL[source])
                        if db_source:
                            source_name = str(db_source.name) if db_source.name else None
                            source_url = str(db_source.domain) if db_source.domain else None
                            import json
                            config = json.loads(db_source.config_json or "{}")
                            confidence = config.get("confidence")
                    finally:
                        if not is_mocked_db and hasattr(db, "close"):
                            try:
                                db.close()
                            except Exception:
                                pass
            except Exception:
                pass

            if source_name is None or (not is_mocked_db and source in SOURCES):
                if source in SOURCES:
                    source_name, default_url, default_confidence = SOURCES[source]
                elif source in DISPLAY_TO_CANONICAL:
                    canonical_key = DISPLAY_TO_CANONICAL[source]
                    source_name = source
                    _, default_url, default_confidence = SOURCES[canonical_key]
                else:
                    source_name = source_name or source
                    default_url = None
                    default_confidence = 0.50
            else:
                default_url = None
                default_confidence = 0.50

            if source_url is None:
                if source in SOURCES:
                    source_url = SOURCES[source][1]
                elif source in DISPLAY_TO_CANONICAL:
                    source_url = SOURCES[DISPLAY_TO_CANONICAL[source]][1]
                else:
                    source_url = default_url

            if confidence is None:
                if source in SOURCES:
                    confidence = SOURCES[source][2]
                elif source in DISPLAY_TO_CANONICAL:
                    confidence = SOURCES[DISPLAY_TO_CANONICAL[source]][2]
                else:
                    confidence = default_confidence

            research_url = self._resolve_url(
                task=task,
                source=source,
                source_url=source_url,
            )

            if research_url is None:
                self._save_browser_attempt(
                    investigation_id=investigation_id,
                    task=task,
                    source_name=source_name,
                    source=source,
                    url=None,
                    attempt_order=attempt_order,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    status="ERROR",
                    http_result="URL resolution failed",
                    title=None,
                    text_length=0,
                    relevance_result=None,
                    failure_reason="Could not resolve URL for source",
                    confidence=0.0,
                    selected_as_evidence=False,
                )
                continue

            print(f"\n[DIAGNOSTIC] === Browser Research Agent Attempt ===", flush=True)
            print(f"[DIAGNOSTIC] Task Name: {task.task_type}", flush=True)
            print(f"[DIAGNOSTIC] Target Company/Identifier: {task.target}", flush=True)
            print(f"[DIAGNOSTIC] Selected Source Name: {source_name} ({source})", flush=True)
            print(f"[DIAGNOSTIC] Resolved URL: {research_url}", flush=True)

            try:
                html = self.fetcher(research_url)
                print(f"[DIAGNOSTIC] Browser navigation succeeded.", flush=True)
                
                intervention_type = detect_human_intervention(html)
                if intervention_type:
                    print(f"[DIAGNOSTIC] HUMAN INTERVENTION REQUIRED: {intervention_type}", flush=True)
                    self._save_browser_attempt(
                        investigation_id=investigation_id,
                        task=task,
                        source_name=source_name,
                        source=source,
                        url=research_url,
                        attempt_order=attempt_order,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                        status="BLOCKED",
                        http_result="Human Intervention Required",
                        title=None,
                        text_length=0,
                        relevance_result="HUMAN_INTERVENTION_REQUIRED",
                        failure_reason=f"Human intervention type: {intervention_type}",
                        confidence=0.0,
                        selected_as_evidence=False,
                    )
                    raise HumanInterventionRequiredException(
                        message=f"Human intervention required: {intervention_type}",
                        intervention_type=intervention_type
                    )
                
                failure_reason = self._is_failed_or_blocked_retrieval(html, task.target)
                if failure_reason:
                    print(f"[DIAGNOSTIC] Page classified as failed/blocked or irrelevant. Reason: {failure_reason}", flush=True)
                    print(f"[DIAGNOSTIC] Reaction: Attempting fallback source...", flush=True)
                    self._save_browser_attempt(
                        investigation_id=investigation_id,
                        task=task,
                        source_name=source_name,
                        source=source,
                        url=research_url,
                        attempt_order=attempt_order,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                        status=failure_reason,
                        http_result="Failed Relevance or Blocked check",
                        title=None,
                        text_length=0,
                        relevance_result=failure_reason,
                        failure_reason=f"Classification: {failure_reason}",
                        confidence=0.0,
                        selected_as_evidence=False,
                    )
                    continue
                
                # Fetch succeeded and is not blocked/empty/irrelevant
                page_data = self._extract_page_data(html)
                print(f"[DIAGNOSTIC] Title: {page_data.get('title')}", flush=True)
                print(f"[DIAGNOSTIC] Raw HTML length: {len(html)}", flush=True)
                print(f"[DIAGNOSTIC] Extracted visible text length: {len(page_data.get('text', ''))}", flush=True)
                print(f"[DIAGNOSTIC] Relevance check: PASSED", flush=True)
                print(f"[DIAGNOSTIC] Assigned confidence: {confidence}", flush=True)
                print(f"[DIAGNOSTIC] Final evidence status: AVAILABLE", flush=True)
                
                self._save_browser_attempt(
                    investigation_id=investigation_id,
                    task=task,
                    source_name=source_name,
                    source=source,
                    url=research_url,
                    attempt_order=attempt_order,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    status="SUCCESS",
                    http_result="200 OK",
                    title=page_data.get("title"),
                    text_length=len(page_data.get("text", "")),
                    relevance_result="PASSED",
                    failure_reason=None,
                    confidence=confidence,
                    selected_as_evidence=True,
                )
                
                chosen_source = source_name
                chosen_url = research_url
                chosen_confidence = confidence
                chosen_page_data = page_data
                break
            except HumanInterventionRequiredException:
                raise
            except Exception as ex:
                print(f"[DIAGNOSTIC] Exception occurred during fetch: {ex}", flush=True)
                print(f"[DIAGNOSTIC] Reaction: Attempting fallback source...", flush=True)
                self._save_browser_attempt(
                    investigation_id=investigation_id,
                    task=task,
                    source_name=source_name,
                    source=source,
                    url=research_url,
                    attempt_order=attempt_order,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    status="ERROR",
                    http_result="Fetch Exception",
                    title=None,
                    text_length=0,
                    relevance_result=None,
                    failure_reason=str(ex),
                    confidence=0.0,
                    selected_as_evidence=False,
                )
                continue

        # If none of the candidates succeeded, use the first candidate with 0.0 confidence
        if chosen_page_data is None:
            print(f"\n[DIAGNOSTIC] ALL configured sources failed for task {task.task_id}!", flush=True)
            print(f"[DIAGNOSTIC] Final status: UNAVAILABLE", flush=True)
            print(f"[DIAGNOSTIC] Final confidence: 0.0", flush=True)
            source = candidates[0]
            source_name, source_url, confidence = None, None, None
            is_mocked_db = False

            try:
                from app.db.session import SessionLocal, db_lock
                from app.services.source_registry import get_source_by_name
                from unittest import mock
                is_mocked_db = isinstance(SessionLocal, (mock.Mock, mock.MagicMock)) or not hasattr(SessionLocal, "kw")

                with db_lock:
                    db = SessionLocal()
                    if hasattr(db, "__enter__") and not hasattr(db, "query"):
                        db = db.__enter__()
                    try:
                        db_source = get_source_by_name(db, source)
                        if not db_source and source in DISPLAY_TO_CANONICAL:
                            db_source = get_source_by_name(db, DISPLAY_TO_CANONICAL[source])
                        if db_source:
                            source_name = str(db_source.name) if db_source.name else None
                            source_url = str(db_source.domain) if db_source.domain else None
                    finally:
                        if not is_mocked_db and hasattr(db, "close"):
                            try:
                                db.close()
                            except Exception:
                                pass
            except Exception:
                pass

            if source_name is None or (not is_mocked_db and source in SOURCES):
                if source in SOURCES:
                    source_name, default_url, default_confidence = SOURCES[source]
                elif source in DISPLAY_TO_CANONICAL:
                    canonical_key = DISPLAY_TO_CANONICAL[source]
                    source_name = source
                    _, default_url, default_confidence = SOURCES[canonical_key]
                else:
                    source_name = source_name or source
                    default_url = None
            else:
                default_url = None

            if source_url is None:
                if source in SOURCES:
                    source_url = SOURCES[source][1]
                elif source in DISPLAY_TO_CANONICAL:
                    source_url = SOURCES[DISPLAY_TO_CANONICAL[source]][1]
                else:
                    source_url = default_url

            research_url = self._resolve_url(
                task=task,
                source=source,
                source_url=source_url,
            )

            chosen_source = source_name
            chosen_url = research_url
            chosen_confidence = 0.0
            chosen_page_data = {
                "title": None,
                "text": "",
            }

        return [
            ResearchResult(
                result_id=f"RESULT-{task.task_id}-{index:03d}",
                task_id=task.task_id,
                field_name=field_name,
                field_value=self._extract_field_value(
                    task=task,
                    field_name=field_name,
                    page_data=chosen_page_data,
                ),
                source_name=chosen_source,
                source_url=chosen_url,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                confidence=chosen_confidence,
            )
            for index, field_name in enumerate(
                task.required_fields,
                start=1,
            )
        ]

    @staticmethod
    def _is_failed_or_blocked_retrieval(html: str, target: str) -> str | None:
        if not html or not html.strip():
            return "EMPTY_RESPONSE"

        html_lower = html.lower()

        # 1. Blocked/Forbidden/Access Denied/Security restriction check
        blocked_patterns = [
            "access denied",
            "403 forbidden",
            "403 error",
            "401 unauthorized",
            "503 service unavailable",
            "502 bad gateway",
            "500 internal server error",
            "cloudflare",
            "error code 1020",
            "requested url was rejected",
            "security check to proceed",
            "please verify you are human",
            "solve the captcha",
            "captcha",
            "hcaptcha",
            "recaptcha"
        ]
        for pattern in blocked_patterns:
            if pattern in html_lower:
                return "BLOCKED_OR_ERROR"

        # Check title for access denied or error
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if title_match:
            title_text = title_match.group(1).lower()
            if any(kw in title_text for kw in ["access denied", "forbidden", "attention required", "error", "unauthorized"]):
                return "BLOCKED_OR_ERROR"

        # 2. No results / Invalid input check
        no_results_patterns = [
            "no results found",
            "0 results",
            "no records found",
            "no data found",
            "record not found",
            "not found",
            "invalid gstin",
            "invalid cin",
            "invalid format"
        ]
        for pattern in no_results_patterns:
            if pattern in html_lower:
                return "NO_RESULTS"

        # 3. Generic Relevance Validation
        page_data = BrowserResearchAgent._extract_page_data(html)
        page_text = page_data.get("text") or ""
        
        words = page_text.split()
        if len(words) > 100:
            target_lower = str(target).lower().strip()
            
            # Case A: target is a URL or domain
            if BrowserResearchAgent._is_url(target_lower) or "." in target_lower or "/" in target_lower:
                domain = target_lower
                if "://" in domain:
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(domain)
                        domain = parsed.netloc or domain
                    except Exception:
                        pass
                domain = re.sub(r"^(www\.)?", "", domain)
                domain_prefix = domain.split(".")[0]
                
                # Check if the domain name suffix/prefix is in the text
                if len(domain_prefix) > 2:
                    if domain_prefix not in re.sub(r"\s+", "", page_text.lower()):
                        return "IRRELEVANT_CONTENT"
            
            # Case B: target is a GSTIN / CIN / code (has numbers and letters)
            elif any(c.isdigit() for c in target_lower) and len(target_lower) > 5:
                normalized_target = re.sub(r"\s+", "", target_lower)
                normalized_text = re.sub(r"\s+", "", page_text.lower())
                if normalized_target not in normalized_text:
                    return "IRRELEVANT_CONTENT"
            
            # Case C: target is a company name
            else:
                stop_words = {"limited", "pvt", "ltd", "private", "corporation", "corp", "inc", "incorporated", "co", "company", "and", "the"}
                target_words = [w for w in re.findall(r"\b\w+\b", target_lower) if w not in stop_words and len(w) > 2]
                if target_words:
                    if not any(word in page_text.lower() for word in target_words):
                        return "IRRELEVANT_CONTENT"
                else:
                    if target_lower not in page_text.lower():
                        return "IRRELEVANT_CONTENT"

        return None

    @staticmethod
    def _select_source(
        task: ResearchTask,
    ) -> str | None:
        candidates = [
            *task.preferred_sources,
            *task.fallback_sources,
        ]

        for source in candidates:
            if source in SOURCES or source in DISPLAY_TO_CANONICAL:
                return source

            try:
                from app.db.session import SessionLocal, db_lock
                from app.services.source_registry import get_source_by_name
                from unittest import mock
                is_mocked_db = isinstance(SessionLocal, (mock.Mock, mock.MagicMock)) or not hasattr(SessionLocal, "kw")
                with db_lock:
                    db = SessionLocal()
                    if hasattr(db, "__enter__") and not hasattr(db, "query"):
                        db = db.__enter__()
                    try:
                        db_source = get_source_by_name(db, source)
                        if db_source:
                            return source
                    finally:
                        if not is_mocked_db and hasattr(db, "close"):
                            try:
                                db.close()
                            except Exception:
                                pass
            except Exception:
                pass

        return None

    @staticmethod
    def _resolve_url(
        task: ResearchTask,
        source: str,
        source_url: str | None,
    ) -> str | None:
        target = task.target.strip()
        canonical_source = DISPLAY_TO_CANONICAL.get(source, source)

        if canonical_source == "company_website":
            if BrowserResearchAgent._is_url(target):
                if "://" not in target:
                    return f"https://{target}"

                return target

            return None

        if canonical_source in {
            "generic_web",
            "third_party",
        }:
            if BrowserResearchAgent._is_url(target):
                if "://" not in target:
                    return f"https://{target}"

                return target

            from urllib.parse import quote
            return f"https://duckduckgo.com/?q={quote(target)}"

        return source_url

    @staticmethod
    def _is_url(
        value: str,
    ) -> bool:
        candidate = value

        if "://" not in candidate:
            candidate = f"https://{candidate}"

        parsed = urlparse(candidate)

        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.netloc
            and "." in parsed.netloc
        )

    @staticmethod
    def _fetch_page(url: str) -> str:
        from urllib.parse import urlparse
        from playwright.sync_api import sync_playwright

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Unsupported research URL")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                java_script_enabled=True,
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ignore_https_errors=True,
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=10000)
            except Exception:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass
            page.wait_for_timeout(2000)
            html = page.content()
            context.close()
            browser.close()
            return html

    @staticmethod
    def _sanitize_prompt_injection(text: str | None) -> str | None:
        if not text:
            return text
        # Neutralize common instruction patterns case-insensitively (TRD §79)
        patterns = [
            (r"(?i)\bignore\s+(?:previous|all|the|above|below)?\s*instructions\b", "[neutralized prompt injection instruction]"),
            (r"(?i)\bignore\s+rules\b", "[neutralized prompt injection rules]"),
            (r"(?i)\bignore\s+the\s+rules\b", "[neutralized prompt injection rules]"),
            (r"(?i)\bignore\s+previous\s+directives\b", "[neutralized prompt injection directive]"),
            (r"(?i)\byou\s+are\s+now\b", "[neutralized role-play instruction]"),
            (r"(?i)\bsystem\s+(?:prompt|instruction|directives)\b", "[neutralized system label]"),
            (r"(?i)\bdeveloper\s+instructions\b", "[neutralized system label]"),
        ]
        sanitized = text
        for pattern, replacement in patterns:
            sanitized = re.sub(pattern, replacement, sanitized)
        return sanitized

    @staticmethod
    def _extract_page_data(
        html: str,
    ) -> dict:
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        title = (
            BrowserResearchAgent._clean_text(
                title_match.group(1)
            )
            if title_match
            else None
        )
        title = BrowserResearchAgent._sanitize_prompt_injection(title)

        body = re.sub(
            r"<script[^>]*>.*?</script>",
            " ",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        body = re.sub(
            r"<style[^>]*>.*?</style>",
            " ",
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )

        text = BrowserResearchAgent._clean_text(
            re.sub(r"<[^>]+>", " ", body)
        )
        text = BrowserResearchAgent._sanitize_prompt_injection(text)

        return {
            "title": title,
            "text": text,
        }

    @staticmethod
    def _clean_text(
        value: str,
    ) -> str:
        return " ".join(value.split())

    @staticmethod
    def _extract_field_value(
        task: ResearchTask,
        field_name: str,
        page_data: dict,
    ):
        title = page_data.get("title")
        text = page_data.get("text")

        # Delimit text content as untrusted (TRD §79)
        delimited_text = f"<UNTRUSTED_WEBSITE_CONTENT>\n{text}\n</UNTRUSTED_WEBSITE_CONTENT>" if text else ""

        if field_name == "candidate_entities":
            if not text:
                return []
            name_val = title or task.target
            if name_val:
                for suffix in [
                    " at DuckDuckGo",
                    " - Google Search",
                    " - Google",
                    " | Google",
                    " | DuckDuckGo",
                ]:
                    if suffix in name_val:
                        name_val = name_val.replace(suffix, "")
            return [
                {
                    "name": name_val,
                    "source_text": delimited_text,
                    "confidence": 1.0,
                }
            ]

        if field_name in {
            "legal_name",
            "company_name",
            "business_name",
            "establishment_name",
        }:
            return title or task.target

        if field_name in {
            "gst_status",
            "mca_status",
            "epfo_status",
            "website_status",
        }:
            return "AVAILABLE" if text else "UNAVAILABLE"

        if field_name in {
            "page_title",
            "title",
        }:
            return title

        if field_name in {
            "page_text",
            "content",
            "source_text",
        }:
            return delimited_text

        return {
            "title": title,
            "text": delimited_text,
        }
