from __future__ import annotations

from app.research.base import (
    BaseResearchProvider,
    clean_legal_name_candidate,
    detect_bot_or_captcha,
    extract_address_from_text,
    extract_date_from_text,
    extract_html_page_data,
    extract_status_from_text,
    http_fetch_direct,
    is_failed_or_blocked_response,
    is_url,
    sanitize_prompt_injection,
    score_candidate_url,
)
from app.research.company_website import CompanyWebsiteResearchProvider
from app.research.dispatcher import ResearchDispatcher, default_dispatcher
from app.research.epfo import EpfoResearchProvider
from app.research.generic_web import GenericWebResearchProvider
from app.research.gst import GstResearchProvider
from app.research.mca import McaResearchProvider
from app.research.source_registry import SourceMetadata, SourceRegistryManager, SourceType, source_registry

__all__ = [
    "BaseResearchProvider",
    "CompanyWebsiteResearchProvider",
    "EpfoResearchProvider",
    "GenericWebResearchProvider",
    "GstResearchProvider",
    "McaResearchProvider",
    "ResearchDispatcher",
    "SourceMetadata",
    "SourceRegistryManager",
    "SourceType",
    "clean_legal_name_candidate",
    "clean_text",
    "default_dispatcher",
    "detect_bot_or_captcha",
    "extract_address_from_text",
    "extract_date_from_text",
    "extract_html_page_data",
    "extract_status_from_text",
    "http_fetch_direct",
    "is_failed_or_blocked_response",
    "is_url",
    "sanitize_prompt_injection",
    "score_candidate_url",
    "source_registry",
]
