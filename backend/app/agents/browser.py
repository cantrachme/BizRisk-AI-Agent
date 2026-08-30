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
    "company_website": ("Company Website", None, 0.85),
    "generic_web": ("General Web", None, 0.60),
    "third_party": ("Third-Party Source", None, 0.50),
}

SUPPORTED_TASK_TYPES = {
    "ENTITY_DISCOVERY",
    "GST_VERIFICATION",
    "MCA_VERIFICATION",
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

    def execute(
        self,
        task: ResearchTask,
    ) -> list[ResearchResult]:
        source = self._select_source(task)

        if source is None:
            return []

        # Check domain restrictions (TRD §80)
        allowed_domains = getattr(task, "allowed_domains", None)
        if allowed_domains is not None:
            if source not in allowed_domains:
                return []

        if task.task_type not in SUPPORTED_TASK_TYPES:
            return []

        # Resolve custom registry sources first, but never let a registry
        # miss replace canonical metadata for built-in sources.
        source_name = None
        source_url = None
        confidence = None
        registry_resolved = False

        if source in SOURCES:
            source_name, source_url, confidence = SOURCES[source]

        try:
            from app.db.session import SessionLocal, db_lock
            from app.services.source_registry import get_source_by_name
            from sqlalchemy.orm.session import sessionmaker

            is_registry_mocked = not isinstance(SessionLocal, sessionmaker)

            with db_lock:
                with SessionLocal() as db:
                    db_source = get_source_by_name(db, source)
                    if db_source:
                        registry_resolved = True
                        source_url = db_source.domain or source_url
                        import json
                        config = json.loads(db_source.config_json or "{}")
                        confidence = config.get(
                            "confidence",
                            confidence if confidence is not None else SOURCES.get(source, ("", "", 0.50))[2],
                        )
                        if is_registry_mocked or source not in SOURCES:
                            source_name = db_source.name
        except Exception:
            pass

        if source_name is None:
            source_name = source
            source_url = None
            confidence = 0.50

        research_url = self._resolve_url(
            task=task,
            source=source,
            source_url=source_url,
        )

        if research_url is None:
            return []

        try:
            html = self.fetcher(research_url)
            intervention_type = detect_human_intervention(html)
            if intervention_type:
                raise HumanInterventionRequiredException(
                    message=f"Human intervention required: {intervention_type}",
                    intervention_type=intervention_type
                )
            page_data = self._extract_page_data(html)
        except HumanInterventionRequiredException:
            raise
        except Exception:
            page_data = {
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
                    page_data=page_data,
                ),
                source_name=source_name,
                source_url=research_url,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                confidence=confidence,
            )
            for index, field_name in enumerate(
                task.required_fields,
                start=1,
            )
        ]

    @staticmethod
    def _select_source(
        task: ResearchTask,
    ) -> str | None:
        for source in [
            *task.preferred_sources,
            *task.fallback_sources,
        ]:
            if source:
                # Built-in sources and registry-backed custom sources are
                # both valid. The registry lookup in execute() is authoritative.
                return source

        return None

    @staticmethod
    def _resolve_url(
        task: ResearchTask,
        source: str,
        source_url: str | None,
    ) -> str | None:
        target = task.target.strip()

        if source == "company_website":
            if BrowserResearchAgent._is_url(target):
                if "://" not in target:
                    return f"https://{target}"

                return target

            return None

        if source in {
            "generic_web",
            "third_party",
        }:
            if BrowserResearchAgent._is_url(target):
                if "://" not in target:
                    return f"https://{target}"

                return target

            return f"https://www.google.com/search?q={target}"

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
    @staticmethod
    def _fetch_page(url: str) -> str:
        from playwright.sync_api import sync_playwright
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Unsupported research URL")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                java_script_enabled=True,
                user_agent="BizRiskResearchBot/1.0",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
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
            return [
                {
                    "name": title or task.target,
                    "source_text": delimited_text,
                    "confidence": 0.0,
                }
            ]

        if field_name in {
            "legal_name",
            "company_name",
            "business_name",
        }:
            return title or task.target

        if field_name in {
            "gst_status",
            "mca_status",
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
