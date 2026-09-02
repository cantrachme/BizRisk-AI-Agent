from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.entity_resolution.scoring import compute_name_similarity
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
    is_url,
)
from app.research.source_registry import source_registry


import logging
logger = logging.getLogger("bizrisk.observability")


class GenericWebResearchProvider(BaseResearchProvider):
    """
    Automated research provider for curated public company directories
    (QuickCompany, Tofler, Zauba Corp, InstaFinancials) and public web records.
    Extracts structured company attributes and candidate entities with Tier 3/4 authority.
    """

    provider_name: str = "GenericWebResearchProvider"
    supported_task_types: set[str] = {
        "THIRD_PARTY_RESEARCH",
        "GENERAL_WEB_RESEARCH",
        "ENTITY_DISCOVERY",
    }

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
        candidate_sources = task.preferred_sources + task.fallback_sources
        if not candidate_sources:
            candidate_sources = ["quickcompany.in", "tofler.in", "zaubacorp.com", "instafinancials.com", "generic_web"]

        chosen_html: Optional[str] = None
        chosen_url: Optional[str] = None
        chosen_source_name: str = "Third-Party Source"
        chosen_confidence: float = 0.50
        chosen_authority_tier: int = 3

        for src in candidate_sources:
            src_meta = source_registry.get_source(src)
            tier = src_meta.authority_tier if src_meta else 3
            base_conf = src_meta.default_confidence if src_meta else 0.50
            disp_name = src_meta.display_name if src_meta else src

            query_url = self._resolve_url(src, target, task.task_type)
            logger.info(
                "[GENERIC_WEB_RESOLVE_URL] Task=%s Source='%s' Target='%s' Resolved_URL='%s' Source_Metadata=%s",
                task.task_id,
                src,
                target,
                query_url,
                src_meta.__dict__ if src_meta else None,
            )
            if not query_url:
                continue

            try:
                html = self.fetcher(query_url)
            except Exception:
                continue

            if not html or not html.strip():
                continue

            # Candidate link discovery on directory search results
            try:
                from urllib.parse import urlparse
                parsed_domain = urlparse(query_url).netloc.lower().replace("www.", "")
                raw_hrefs = re.findall(r'href=["\']([^"\'#?]+(?:/[^"\'#?]+)*)["\']', html, re.IGNORECASE)
                cand_links = []
                for href in raw_hrefs:
                    if any(pattern in href.lower() for pattern in ["/company/", "/companysearchresults/", "/gstin/"]):
                        full_url = href if href.startswith("http") else f"https://www.{parsed_domain}/{href.lstrip('/')}"
                        if parsed_domain in full_url.lower() and full_url not in cand_links and full_url != query_url:
                            cand_links.append(full_url)

                logger.info(
                    "[GENERIC_WEB_LINK_DISCOVERY] Task=%s Initial_URL='%s' Candidate_URLs=%s",
                    task.task_id,
                    query_url,
                    cand_links,
                )

                if cand_links:
                    from app.research.base import score_candidate_url
                    scored_cands = []
                    for link in cand_links:
                        sc, _, rel = score_candidate_url(link, task.target, task.task_type)
                        if sc >= 0.40 or rel in {"TARGET_ENTITY", "RELATED_ENTITY"}:
                            scored_cands.append((sc, link))

                    logger.info(
                        "[GENERIC_WEB_LINK_SCORING] Task=%s Candidate_Scores=%s",
                        task.task_id,
                        scored_cands,
                    )

                    if scored_cands:
                        scored_cands.sort(key=lambda x: x[0], reverse=True)
                        best_url = scored_cands[0][1]
                        sub_html = self.fetcher(best_url)
                        if sub_html and not detect_bot_or_captcha(sub_html) and not is_failed_or_blocked_response(sub_html, target):
                            html = sub_html
                            query_url = best_url
            except Exception:
                pass

            logger.info(
                "[GENERIC_WEB_SELECTED_URL] Task=%s Final_Selected_URL='%s'",
                task.task_id,
                query_url,
            )

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
            val, basis = self._extract_field(task, field_name, page_data, chosen_url)
            field_conf = chosen_confidence

            if isinstance(val, str) and val in {"NOT_FOUND", "UNAVAILABLE", "SOURCE_UNAVAILABLE"}:
                field_conf = 0.0

            if chosen_confidence == 0.0 or not page_data.get("text"):
                verif_status = "SOURCE_UNAVAILABLE"
                auth_tier = 4
            elif isinstance(val, str) and val in {"NOT_FOUND", "UNAVAILABLE"}:
                verif_status = "NOT_FOUND"
                auth_tier = chosen_authority_tier
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

    def _resolve_url(self, source: str, target: str, task_type: Optional[str] = None) -> Optional[str]:
        src_meta = source_registry.get_source(source)
        if src_meta and hasattr(src_meta, "resolve_target_url"):
            resolved = src_meta.resolve_target_url(target, task_type=task_type)
            if resolved:
                return resolved

        if is_url(target):
            return target if "://" in target else f"https://{target}"

        src_lower = source.lower()
        clean_name = re.sub(r"(?i)\s+(?:in\s+[A-Za-z]+|mca\s+company\s+registration|epfo\s+establishment|official\s*website|company\s*registration|search|portal|master\s*data)\b", "", target).strip()
        clean_target = re.sub(r"[^a-zA-Z0-9\s-]", "", clean_name).strip().replace(" ", "-").lower()

        if "quickcompany" in src_lower:
            return f"https://www.quickcompany.in/company/{clean_target}"
        if "tofler" in src_lower:
            return f"https://www.tofler.in/company/{clean_target}"
        if "zaubacorp" in src_lower:
            return f"https://www.zaubacorp.com/company/{clean_target}"
        if "instafinancials" in src_lower:
            return f"https://www.instafinancials.com/company/{clean_target}"

        return f"https://www.quickcompany.in/company/{clean_target}"

    def _extract_field(
        self,
        task: ResearchTask,
        field_name: str,
        page_data: dict,
        source_url: Optional[str],
    ) -> Tuple[Any, Optional[str]]:
        text = page_data.get("text") or ""
        title = page_data.get("title") or ""

        delimited_text = f"<UNTRUSTED_WEBSITE_CONTENT>\n{text}\n</UNTRUSTED_WEBSITE_CONTENT>" if text else ""

        if field_name == "candidate_entities":
            if not text and not title:
                return [], "No content available for candidate entity discovery"
            name_val = title or task.target
            name_sim = compute_name_similarity(task.target, name_val)
            cand_conf = 0.85 if name_sim >= 0.80 else round(max(0.40, name_sim), 2)
            return [
                {
                    "name": name_val,
                    "source_text": delimited_text,
                    "confidence": cand_conf,
                }
            ], "Discovered candidate entity from directory profile"

        if field_name in {"legal_name", "company_name", "business_name"}:
            if not text:
                return "NOT_FOUND", "No text available"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for line in lines:
                match = re.search(r"(?:legal\s+name(?:\s+of\s+(?:business|taxpayer|company))?|company\s+name|name\s+of\s+company|establishment\s+name|trade\s+name)\s*[:\-]?\s*([A-Za-z0-9\s.,&()-]+)", line, re.IGNORECASE)
                if match and len(match.group(1).strip()) > 3:
                    cand = clean_legal_name_candidate(match.group(1).strip())
                    if cand:
                        return cand, f"Extracted legal name from line: '{line}'"
            if title:
                cand = clean_legal_name_candidate(title)
                if cand:
                    return cand, "Normalized company name from page title"
            return "NOT_FOUND", "No company name found"

        if field_name in {"company_status", "status"}:
            if not text:
                return "NOT_FOUND", "No text available"
            for line in text.split("\n"):
                line_lower = line.lower()
                for keyword in ["active", "inactive", "cancelled", "suspended", "allocated", "struck off"]:
                    if keyword in line_lower:
                        return keyword.upper(), f"Matched status keyword '{keyword.upper()}'"
            return "NOT_FOUND", "No status keyword matched"

        if field_name in {"registered_address", "address"}:
            addr = extract_address_from_text(text)
            if addr == "NOT_FOUND":
                return "NOT_FOUND", "No address pattern matched in directory text"
            return addr, "Extracted address from directory text"

        if field_name in {"business_activity", "activity"}:
            if not text:
                return "NOT_FOUND", "No text available"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for line in lines:
                match = re.search(r"(?:business\s+activity|nature\s+of\s+business|activity)\s*[:\-]?\s*([A-Za-z0-9\s.,&()/-]+)", line, re.IGNORECASE)
                if match and len(match.group(1).strip()) > 3:
                    return match.group(1).strip(), "Extracted business activity from directory text"
            return "NOT_FOUND", "No business activity found"

        if field_name in {"page_title", "title"}:
            return title, "Raw page title"

        if field_name in {"page_text", "content", "source_text"}:
            return delimited_text, "Delimited page text"

        return "NOT_FOUND", "Unknown field"
