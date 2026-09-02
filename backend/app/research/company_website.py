from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from app.graph.state import ResearchResult, ResearchTask
from app.research.base import (
    BaseResearchProvider,
    classify_entity_relationship,
    clean_legal_name_candidate,
    detect_bot_or_captcha,
    extract_address_from_text,
    extract_date_from_text,
    extract_html_page_data,
    http_fetch_direct,
    is_failed_or_blocked_response,
    is_url,
    score_candidate_url,
)
from app.research.source_registry import source_registry


class CompanyWebsiteResearchProvider(BaseResearchProvider):
    """
    Automated research provider for verifying official company websites.
    Directly checks HTTP/HTTPS accessibility, entity relationship, contact information,
    and business claims without search-engine dependency.
    """

    provider_name: str = "CompanyWebsiteResearchProvider"
    supported_task_types: set[str] = {"WEBSITE_VERIFICATION"}

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
        is_direct_url = is_url(target)
        website_url = target if is_direct_url else None
        if website_url and "://" not in website_url:
            website_url = f"https://{website_url}"

        # If target is not a URL, derive domain candidate deterministically if possible
        if not website_url:
            clean_name = re.sub(r"(?i)\s+(?:official\s*website|website|in\s*india|company\s*registration|pvt|ltd|limited|private|llp|corp|inc)\b", "", target).strip()
            clean_slug = re.sub(r"[^a-zA-Z0-9]", "", clean_name).lower()
            if clean_slug:
                website_url = f"https://www.{clean_slug}.com"

        chosen_html: Optional[str] = None
        chosen_url: Optional[str] = None
        chosen_confidence: float = 0.0
        chosen_page_data: Dict[str, Any] = {"title": None, "text": ""}
        actual_rel: str = "UNKNOWN"

        if website_url:
            # Score candidate URL before fetch
            cand_score, reason, expected_rel = score_candidate_url(website_url, target, task.task_type)
            if cand_score >= 0.40 or is_direct_url:
                try:
                    html = self.fetcher(website_url)
                    if html and html.strip():
                        challenge = detect_bot_or_captcha(html)
                        failure = is_failed_or_blocked_response(html, target)
                        
                        if not challenge and not failure:
                            page_data = extract_html_page_data(html)
                            parsed_domain = urlparse(website_url).netloc.replace("www.", "")
                            
                            actual_rel = classify_entity_relationship(
                                target=target,
                                domain=parsed_domain,
                                page_title=page_data.get("title") or "",
                                page_text=page_data.get("text") or "",
                            )

                            if actual_rel in {"TARGET_ENTITY", "RELATED_ENTITY"}:
                                chosen_html = html
                                chosen_url = website_url
                                chosen_page_data = page_data
                                chosen_page_data["url"] = website_url
                                chosen_page_data["relationship"] = actual_rel
                                chosen_confidence = 0.85 if actual_rel == "TARGET_ENTITY" else 0.50
                except Exception:
                    pass

        retrieved_time = datetime.now(timezone.utc).isoformat()
        results: List[ResearchResult] = []

        for index, field_name in enumerate(task.required_fields, start=1):
            val, basis = self._extract_field(task, field_name, chosen_page_data, chosen_url)
            field_conf = chosen_confidence

            if isinstance(val, str) and val in {"NOT_FOUND", "UNAVAILABLE", "SOURCE_UNAVAILABLE"}:
                field_conf = 0.0

            if chosen_confidence == 0.0 or not chosen_page_data.get("text"):
                verif_status = "SOURCE_UNAVAILABLE"
                auth_tier = 4
            elif isinstance(val, str) and val in {"NOT_FOUND", "UNAVAILABLE"}:
                verif_status = "NOT_FOUND"
                auth_tier = 2
            else:
                verif_status = "VERIFIED" if field_conf >= 0.70 else "UNVERIFIED"
                auth_tier = 2

            results.append(
                ResearchResult(
                    result_id=f"RESULT-{task.task_id}-{index:03d}",
                    task_id=task.task_id,
                    field_name=field_name,
                    field_value=val,
                    source_name="Company Website",
                    source_url=chosen_url,
                    retrieved_at=retrieved_time,
                    confidence=field_conf,
                    evidence_basis=basis,
                    verification_status=verif_status,
                    authority_tier=auth_tier,
                )
            )

        return results

    def _extract_field(
        self,
        task: ResearchTask,
        field_name: str,
        page_data: dict,
        source_url: Optional[str],
    ) -> Tuple[Any, Optional[str]]:
        text = page_data.get("text") or ""
        title = page_data.get("title") or ""
        url = page_data.get("url") or source_url

        if field_name in {"website_status", "status"}:
            if not text or is_failed_or_blocked_response(text, ""):
                return "UNAVAILABLE", "Website not accessible or returned empty content"
            return "AVAILABLE", "Official company website accessible and active"

        if field_name in {"page_title", "title"}:
            if title:
                return title, "Extracted official website page title"
            return "NOT_FOUND", "No page title found on company website"

        if field_name in {"website_url", "website", "domain"}:
            if url:
                return url, "Verified official company website URL"
            return "NOT_FOUND", "No valid official company website URL verified"

        if field_name in {"contact_address", "address", "registered_address"}:
            addr = extract_address_from_text(text)
            if addr == "NOT_FOUND":
                return "NOT_FOUND", "No contact address pattern found on website"
            return addr, "Extracted contact address from company website"

        if field_name in {"established_year", "incorporation_date", "founded"}:
            dt = extract_date_from_text(text)
            if dt == "NOT_FOUND":
                return "NOT_FOUND", "No founding year pattern found on website"
            return dt, "Extracted establishment year from website"

        if field_name in {"legal_name", "company_name", "business_name"}:
            if title:
                cand = clean_legal_name_candidate(title)
                if cand:
                    return cand, "Normalized company name from website title"
            return "NOT_FOUND", "No company name extracted from website title"

        if field_name in {"meta_description", "description", "business_activity"}:
            if text:
                for line in text.split("\n"):
                    match = re.search(r"(?:business\s+activity|nature\s+of\s+business|products?\s*(?:and|&)\s*services?|services?|activity)\s*[:\-]?\s*([A-Za-z0-9\s.,&()/-]+)", line, re.IGNORECASE)
                    if match and len(match.group(1).strip()) > 5:
                        return match.group(1).strip()[:250], f"Extracted business activity from line: '{line}'"
                first_para = next((p.strip() for p in text.split("\n") if len(p.strip()) > 20 and (not title or not p.strip().startswith(title))), None)
                if first_para:
                    return first_para[:250], "Extracted description from website content"
            return "NOT_FOUND", "No description available"

        return "NOT_FOUND", "Unknown field"
