from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import re

from app.graph.state import ResearchResult, ResearchTask


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

        if task.task_type not in SUPPORTED_TASK_TYPES:
            return []

        source_name, source_url, confidence = SOURCES[source]

        research_url = self._resolve_url(
            task=task,
            source=source,
            source_url=source_url,
        )

        if research_url is None:
            return []

        try:
            html = self.fetcher(research_url)
            page_data = self._extract_page_data(html)
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
            if source in SOURCES:
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
    def _fetch_page(
        url: str,
    ) -> str:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(compatible; BizRiskAI/1.0)"
                )
            },
        )

        with urlopen(
            request,
            timeout=10,
        ) as response:
            charset = response.headers.get_content_charset() or "utf-8"

            return response.read().decode(
                charset,
                errors="replace",
            )

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

        if field_name == "candidate_entities":
            return [
                {
                    "name": title or task.target,
                    "source_text": text,
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
            return text

        return {
            "title": title,
            "text": text,
        }
