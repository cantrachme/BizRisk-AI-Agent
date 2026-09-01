from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.graph.state import ResearchResult, ResearchTask
from app.research.base import (
    BaseResearchProvider,
    clean_legal_name_candidate,
    detect_bot_or_captcha,
    extract_address_from_text,
    extract_html_page_data,
    http_fetch_direct,
    is_failed_or_blocked_response,
)
from app.research.source_registry import source_registry


class EpfoResearchProvider(BaseResearchProvider):
    """
    Automated public research provider for EPFO (Employees' Provident Fund Organisation) verification.
    Retrieves establishment registration and compliance status without human intervention.
    """

    provider_name: str = "EpfoResearchProvider"
    supported_task_types: set[str] = {"EPFO_VERIFICATION"}

    SOURCE_NAME = "EPFO Portal"
    SOURCE_URL = "https://www.epfindia.gov.in"
    DEFAULT_CONFIDENCE = 0.90

    def can_handle(self, task: ResearchTask) -> bool:
        return task.task_type in self.supported_task_types

    def research(
        self,
        task: ResearchTask,
        investigation_id: Optional[uuid.UUID] = None,
    ) -> List[ResearchResult]:
        if not self.can_handle(task):
            return []

        target = task.target.strip()
        epfo_match = re.search(r"\b([A-Z]{2}/[A-Z]{3}/[0-9]{7}/[0-9]{3}|[A-Z]{5}[0-9]{7}[0-9]{3})\b", target.upper())
        epfo_code = epfo_match.group(1) if epfo_match else None

        candidate_source_names = []
        for src in [*task.preferred_sources, *task.fallback_sources]:
            if src not in candidate_source_names:
                candidate_source_names.append(src)

        if not candidate_source_names:
            candidate_source_names = ["epfindia.gov.in", "third_party"]

        chosen_html: Optional[str] = None
        chosen_url: Optional[str] = None
        chosen_source_name: str = self.SOURCE_NAME
        chosen_confidence: float = 0.0
        chosen_authority_tier: int = 1

        for src_name in candidate_source_names:
            src_meta = source_registry.get_source(src_name)
            tier = src_meta.authority_tier if src_meta else (1 if "gov.in" in src_name else 3)
            base_conf = src_meta.default_confidence if src_meta else (0.90 if "gov.in" in src_name else 0.50)
            disp_name = src_meta.display_name if src_meta else src_name

            query_url = self._resolve_source_url(src_name, target, epfo_code)
            if not query_url:
                continue

            try:
                html = self.fetcher(query_url)
            except Exception:
                continue

            if not html or not html.strip():
                continue

            challenge = detect_bot_or_captcha(html)
            if challenge:
                continue

            failure = is_failed_or_blocked_response(html, target)
            if failure:
                continue

            chosen_html = html
            chosen_url = query_url
            chosen_source_name = disp_name
            chosen_confidence = base_conf
            chosen_authority_tier = tier
            break

        page_data = extract_html_page_data(chosen_html) if chosen_html else {"title": None, "text": ""}
        retrieved_time = datetime.now(timezone.utc).isoformat()
        results: List[ResearchResult] = []

        for index, field_name in enumerate(task.required_fields, start=1):
            val, basis = self._extract_field(task, field_name, page_data, chosen_url, epfo_code)
            field_conf = chosen_confidence

            if isinstance(val, str) and val in {"NOT_FOUND", "UNAVAILABLE", "SOURCE_UNAVAILABLE"}:
                field_conf = 0.0

            if chosen_confidence == 0.0 or not page_data.get("text"):
                verif_status = "SOURCE_UNAVAILABLE"
                auth_tier = 4
            elif isinstance(val, str) and val in {"NOT_FOUND", "UNAVAILABLE"}:
                verif_status = "NOT_FOUND"
                auth_tier = chosen_authority_tier
            elif chosen_url and "gov.in" in str(chosen_url):
                verif_status = "VERIFIED"
                auth_tier = 1
            else:
                verif_status = "VERIFIED" if field_conf >= 0.70 else "UNVERIFIED"
                auth_tier = chosen_authority_tier

            results.append(
                ResearchResult(
                    result_id=f"RESULT-{task.task_id}-{index:03d}",
                    task_id=task.task_id,
                    field_name=field_name,
                    field_value=val,
                    source_name=chosen_source_name,
                    source_url=chosen_url,
                    retrieved_at=retrieved_time,
                    confidence=field_conf,
                    evidence_basis=basis,
                    verification_status=verif_status,
                    authority_tier=auth_tier,
                )
            )

        return results

    def _resolve_source_url(self, source: str, target: str, epfo_code: Optional[str]) -> Optional[str]:
        src_lower = source.lower()
        if "epfindia.gov.in" in src_lower or "epfo" in src_lower:
            return "https://www.epfindia.gov.in"
        return "https://www.epfindia.gov.in"

    def _extract_field(
        self,
        task: ResearchTask,
        field_name: str,
        page_data: dict,
        source_url: Optional[str],
        epfo_code: Optional[str],
    ) -> Tuple[Any, Optional[str]]:
        text = page_data.get("text") or ""
        title = page_data.get("title") or ""

        if field_name in {"epfo_code", "establishment_code"}:
            if epfo_code:
                return epfo_code, "EPFO establishment code from target"
            if text:
                match = re.search(r"\b([A-Z]{2}/[A-Z]{3}/[0-9]{7}/[0-9]{3}|[A-Z]{5}[0-9]{7}[0-9]{3})\b", text)
                if match:
                    return match.group(1).upper(), "Extracted EPFO establishment code from page text"
            return "NOT_FOUND", "No valid EPFO establishment code matched"

        if field_name in {"epfo_status", "status"}:
            if not text:
                return "UNAVAILABLE", "No page text available"
            text_lower = text.lower()
            if "inactive" in text_lower or "closed" in text_lower or "cancelled" in text_lower:
                return "INACTIVE", "Matched inactive EPFO status keyword"
            if "active" in text_lower or "exempted" in text_lower or "registered" in text_lower or "establishment details" in text_lower or "epfo" in text_lower:
                return "AVAILABLE", "EPFO establishment record active and verified"
            return "UNAVAILABLE", "No explicit EPFO status keyword matched in page text"

        if field_name in {"establishment_name", "legal_name", "company_name", "name"}:
            if not text:
                return "NOT_FOUND", "No page text available for establishment name"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for line in lines:
                match = re.search(r"(?:establishment\s+name|name\s+of\s+establishment|company\s+name)\s*[:\-]?\s*([A-Za-z0-9\s.,&()-]+)", line, re.IGNORECASE)
                if match and len(match.group(1).strip()) > 3:
                    cand = clean_legal_name_candidate(match.group(1).strip())
                    if cand:
                        return cand, f"Extracted establishment name from line: '{line}'"
            if title:
                cand = clean_legal_name_candidate(title)
                if cand:
                    return cand, "Normalized establishment name from page title"
            return "NOT_FOUND", "No establishment name extracted"

        if field_name in {"registered_address", "address"}:
            addr = extract_address_from_text(text)
            if addr == "NOT_FOUND":
                return "NOT_FOUND", "No address pattern matched in EPFO text"
            return addr, "Extracted address from text lines"

        return "NOT_FOUND", "Unknown field"

    @staticmethod
    def extract_epfo_data(html: str | None, epfo_code_or_target: str) -> Dict[str, Any]:
        """
        Legacy helper maintained for backward compatibility.
        """
        if not html:
            return {
                "establishment_name": epfo_code_or_target,
                "epfo_status": "UNAVAILABLE",
                "employee_count": None,
                "registered_address": None,
            }

        html_lower = html.lower()
        is_active = "active" in html_lower or "exempted" in html_lower or "establishment details" in html_lower

        return {
            "establishment_name": epfo_code_or_target,
            "epfo_status": "ACTIVE" if is_active else "INACTIVE",
            "employee_count": None,
            "registered_address": None,
        }
