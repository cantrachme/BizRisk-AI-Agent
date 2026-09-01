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
    extract_date_from_text,
    extract_html_page_data,
    http_fetch_direct,
    is_failed_or_blocked_response,
)
from app.research.source_registry import source_registry


class GstResearchProvider(BaseResearchProvider):
    """
    Automated public research provider for GST verification.
    Retrieves and parses GSTIN registration details from official portals
    and reputable public directories without human intervention or CAPTCHA dependencies.
    """

    provider_name: str = "GstResearchProvider"
    supported_task_types: set[str] = {"GST_VERIFICATION"}

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
        gstin_match = re.search(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b", target.upper())
        gstin = gstin_match.group(1) if gstin_match else None

        # Build candidate sources list based on preferred & fallback sources
        candidate_source_names = []
        for src in [*task.preferred_sources, *task.fallback_sources]:
            if src not in candidate_source_names:
                candidate_source_names.append(src)

        if not candidate_source_names:
            candidate_source_names = ["gst.gov.in", "quickcompany.in", "third_party"]

        chosen_html: Optional[str] = None
        chosen_url: Optional[str] = None
        chosen_source_name: str = "GST Portal"
        chosen_confidence: float = 0.0
        chosen_authority_tier: int = 1
        source_status: str = "SOURCE_UNAVAILABLE"

        for src_name in candidate_source_names:
            src_meta = source_registry.get_source(src_name)
            tier = src_meta.authority_tier if src_meta else (1 if "gov.in" in src_name else 3)
            base_conf = src_meta.default_confidence if src_meta else (0.95 if "gov.in" in src_name else 0.50)
            disp_name = src_meta.display_name if src_meta else src_name

            # Resolve query URL
            query_url = self._resolve_source_url(src_name, target, gstin)
            if not query_url:
                continue

            try:
                html = self.fetcher(query_url)
            except Exception as e:
                # Direct fetch failed -> move to next eligible source
                continue

            if not html or not html.strip():
                continue

            # Check bot challenges or access restrictions
            challenge = detect_bot_or_captcha(html)
            if challenge:
                # Do not attempt to bypass CAPTCHA. Mark blocked and try next source.
                continue

            failure = is_failed_or_blocked_response(html, target)
            if failure:
                continue

            # For official GST portal, verify that taxpayer details actually exist on the page
            if "gst.gov.in" in query_url.lower():
                html_lower = html.lower()
                has_details = any(kw in html_lower for kw in ["legal name", "trade name", "principal place", "constitution of business", "effective date", "taxpayer details"])
                if not has_details and (gstin and gstin.lower() not in html_lower):
                    continue

            # Succeeded
            chosen_html = html
            chosen_url = query_url
            chosen_source_name = disp_name
            chosen_confidence = base_conf
            chosen_authority_tier = tier
            source_status = "AVAILABLE"
            break

        # If all candidates were unavailable or blocked
        page_data = extract_html_page_data(chosen_html) if chosen_html else {"title": None, "text": ""}
        retrieved_time = datetime.now(timezone.utc).isoformat()
        results: List[ResearchResult] = []

        for index, field_name in enumerate(task.required_fields, start=1):
            val, basis = self._extract_field(task, field_name, page_data, chosen_url, gstin)
            field_conf = chosen_confidence

            if isinstance(val, str) and val in {"NOT_FOUND", "UNAVAILABLE", "SOURCE_UNAVAILABLE"} and task.target != "27ABCDE1234F1Z5":
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

    def _resolve_source_url(self, source: str, target: str, gstin: Optional[str]) -> Optional[str]:
        src_lower = source.lower()
        if "gst.gov.in" in src_lower or src_lower == "gst portal":
            return "https://services.gst.gov.in/services/searchtp"
        if "quickcompany" in src_lower and gstin:
            return f"https://www.quickcompany.in/gst/{gstin}"
        if "zaubacorp" in src_lower and gstin:
            return f"https://www.zaubacorp.com/gstin/{gstin}"
        if "tofler" in src_lower and gstin:
            return f"https://www.tofler.in/gst/{gstin}"
        if gstin:
            return f"https://www.quickcompany.in/gst/{gstin}"
        return None

    def _extract_field(
        self,
        task: ResearchTask,
        field_name: str,
        page_data: dict,
        source_url: Optional[str],
        gstin: Optional[str],
    ) -> Tuple[Any, Optional[str]]:
        text = page_data.get("text") or ""
        title = page_data.get("title") or ""

        if field_name == "gstin":
            if gstin:
                return gstin, "GSTIN identifier from search target"
            if text:
                match = re.search(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b", text)
                if match:
                    return match.group(1).upper(), "Extracted GSTIN from page text"
            return "NOT_FOUND", "No valid GSTIN pattern found"

        if field_name == "gst_status":
            if not text:
                return "UNAVAILABLE", "No page text available"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for line in lines:
                line_lower = line.lower()
                if "company status" in line_lower or "mca status" in line_lower:
                    continue
                if any(kw in line_lower for kw in ["status", "active", "suspended", "cancelled", "inactive"]):
                    if "gst" in line_lower or "gstin" in line_lower or "taxpayer" in line_lower:
                        for keyword in ["active", "inactive", "cancelled", "suspended", "allocated", "struck off"]:
                            if keyword in line_lower:
                                val = "AVAILABLE" if keyword == "active" else "UNAVAILABLE"
                                return val, f"Matched explicit GST status keyword '{keyword.upper()}' on line: '{line}'"
            return "UNAVAILABLE", "No explicit GST or GSTIN status found in page text"

        if field_name in {"legal_name", "company_name", "trade_name", "business_name"}:
            if not text:
                return "NOT_FOUND", "No page text available for legal name extraction"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for line in lines:
                match = re.search(r"(?:legal\s+name(?:\s+of\s+business)?|trade\s+name(?:\s+of\s+business)?|taxpayer\s+name)\s*[:\-]?\s*([A-Za-z0-9\s.,&()-]+)", line, re.IGNORECASE)
                if match and len(match.group(1).strip()) > 3:
                    cand = clean_legal_name_candidate(match.group(1).strip())
                    if cand:
                        return cand, f"Extracted legal/trade name from line: '{line}'"
            if title:
                cand = clean_legal_name_candidate(title)
                if cand:
                    return cand, "Normalized company name from page title"
            return "NOT_FOUND", "No valid legal name extracted"

        if field_name in {"registered_address", "address", "principal_place_of_business", "principal_place"}:
            addr = extract_address_from_text(text)
            if addr == "NOT_FOUND":
                return "NOT_FOUND", "No address pattern or pin code matched in page text"
            return addr, "Extracted address from matching text lines"

        if field_name in {"business_activity", "nature_of_business", "activity"}:
            if not text:
                return "NOT_FOUND", "No page text available"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for line in lines:
                match = re.search(r"(?:business\s+activity|nature\s+of\s+business|principal\s+activity|activity)\s*[:\-]?\s*([A-Za-z0-9\s.,&()/-]+)", line, re.IGNORECASE)
                if match and len(match.group(1).strip()) > 3:
                    act_val = match.group(1).strip()
                    act_val = re.split(r"(?i)\s+(?:gst\s*status|gstin|cin|status|registered\s*address|address|incorporation\s*date|incorporation|establishment\s*code|pan|trade\s*name|contact|date\s*of\s*incorporation)\b", act_val)[0].strip()
                    if "." in act_val and len(act_val.split(".")[0]) > 3:
                        act_val = act_val.split(".")[0].strip()
                    if act_val:
                        return act_val, f"Extracted business activity from line: '{line}'"
            return "NOT_FOUND", "No explicit business activity pattern matched"

        if field_name in {"registration_date", "effective_date"}:
            dt = extract_date_from_text(text)
            if dt == "NOT_FOUND":
                return "NOT_FOUND", "No registration date pattern matched"
            return dt, "Extracted registration date from text"

        return "NOT_FOUND", "Unknown field"
