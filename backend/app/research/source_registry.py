from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class SourceType(str, Enum):
    GOVERNMENT = "GOVERNMENT"
    GOVERNMENT_OPEN_DATA = "GOVERNMENT_OPEN_DATA"
    OFFICIAL_WEBSITE = "OFFICIAL_WEBSITE"
    THIRD_PARTY_REGISTRY = "THIRD_PARTY_REGISTRY"
    PUBLIC_DIRECTORY = "PUBLIC_DIRECTORY"


@dataclass
class SourceMetadata:
    source_id: str
    name: str
    display_name: str
    source_type: SourceType
    authority_tier: int
    supported_task_types: List[str]
    base_url: Optional[str] = None
    enabled: bool = True
    requires_human_intervention: bool = False
    supports_direct_http: bool = True
    supports_browser: bool = True
    priority: int = 1
    default_confidence: float = 0.50
    config: Dict = field(default_factory=dict)

    def get_candidate_urls(self, target: str, task_type: Optional[str] = None) -> List[str]:
        """
        Dynamically generates an ordered list of candidate URLs for this source
        using available identifiers (CIN, slug/name, GSTIN, search queries).
        """
        if not target or not isinstance(target, str):
            return [self.base_url] if self.base_url else []

        target_clean = target.strip()
        cin_match = re.search(r"\b([UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6})\b", target_clean.upper())
        gstin_match = re.search(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b", target_clean.upper())
        cin = cin_match.group(1) if cin_match else None
        gstin = gstin_match.group(1) if gstin_match else None

        is_direct_url = bool(
            target_clean.startswith(("http://", "https://"))
            or (("." in target_clean) and (" " not in target_clean) and ("/" in target_clean or target_clean.endswith((".com", ".in", ".org", ".net", ".io", ".co"))))
        )

        if self.name == "company_website" or self.source_type == SourceType.OFFICIAL_WEBSITE or task_type == "WEBSITE_VERIFICATION":
            if is_direct_url:
                url = target_clean if "://" in target_clean else f"https://{target_clean}"
                return [url]
            clean_name = re.sub(r"(?i)\s+(?:official\s*website|website|in\s+[A-Za-z]+|company\s*registration|pvt|ltd|limited|private|llp|corp|inc)\b", "", target_clean).strip()
            slug = re.sub(r"[^a-zA-Z0-9]", "", clean_name).lower()
            if slug:
                return [f"https://www.{slug}.com", f"https://{slug}.com", f"https://www.{slug}.in"]
            return []

        if is_direct_url and self.source_type != SourceType.GOVERNMENT:
            url = target_clean if "://" in target_clean else f"https://{target_clean}"
            return [url]

        # Clean business name slug - strictly strip location phrases and query terms
        clean_name = target_clean
        if gstin:
            clean_name = clean_name.replace(gstin, "")
        if cin:
            clean_name = clean_name.replace(cin, "")
        clean_name = re.sub(r"(?i)\s+(?:in\s+[A-Za-z]+|mca\s+company\s+registration|epfo\s+establishment|official\s*website|company\s*registration|search|portal|master\s*data)\b", "", clean_name).strip()
        clean_name = re.sub(r"[^a-zA-Z0-9\s-]", "", clean_name).strip()
        slug = re.sub(r"\s+", "-", clean_name).strip("-").lower()

        candidates: List[str] = []

        # Government Portals
        if self.base_url and any(gov in self.base_url for gov in ["services.gst.gov.in", "mca.gov.in", "epfindia.gov.in"]):
            return [self.base_url]

        # 1. Primary configured patterns
        if cin and slug and self.config.get("cin_name_url_pattern"):
            try:
                candidates.append(self.config["cin_name_url_pattern"].format(slug=slug, cin=cin))
            except Exception:
                pass

        if slug and self.config.get("name_url_pattern"):
            try:
                candidates.append(self.config["name_url_pattern"].format(slug=slug))
            except Exception:
                pass

        if cin and self.config.get("cin_url_pattern"):
            try:
                candidates.append(self.config["cin_url_pattern"].format(cin=cin))
            except Exception:
                pass

        if gstin and self.config.get("gstin_url_pattern"):
            try:
                candidates.append(self.config["gstin_url_pattern"].format(gstin=gstin))
            except Exception:
                pass

        if self.config.get("url_template"):
            try:
                candidates.append(self.config["url_template"].format(slug=slug or target_clean, cin=cin or "", gstin=gstin or ""))
            except Exception:
                pass

        # 2. Alternative search or directory patterns
        query_val = cin or slug or target_clean
        if query_val and self.config.get("search_url_pattern"):
            try:
                candidates.append(self.config["search_url_pattern"].format(query=query_val, slug=slug or "", cin=cin or ""))
            except Exception:
                pass

        # 3. Base URL fallbacks
        if self.base_url:
            if slug:
                candidates.append(f"{self.base_url.rstrip('/')}/company/{slug}")
            if cin:
                candidates.append(f"{self.base_url.rstrip('/')}/company/{cin}")
            candidates.append(self.base_url)

        # Deduplicate while preserving priority order
        seen = set()
        unique_candidates = []
        for url in candidates:
            if url and url not in seen:
                seen.add(url)
                unique_candidates.append(url)

        return unique_candidates

    def resolve_target_url(self, target: str, task_type: Optional[str] = None) -> Optional[str]:
        """
        Dynamically resolves the primary company or research page URL.
        """
        candidates = self.get_candidate_urls(target, task_type)
        return candidates[0] if candidates else self.base_url


class SourceRegistryManager:
    """
    Centralized registry of all public and official research sources.
    Defines capabilities, authority tiers, priorities, and task routing.
    """

    def __init__(self):
        self._sources: Dict[str, SourceMetadata] = {}
        self._initialize_default_sources()

    def _initialize_default_sources(self):
        default_sources = [
            SourceMetadata(
                source_id="gst-gov-in",
                name="gst.gov.in",
                display_name="GST Portal",
                source_type=SourceType.GOVERNMENT,
                authority_tier=1,
                supported_task_types=["GST_VERIFICATION"],
                base_url="https://services.gst.gov.in/services/searchtp",
                priority=1,
                default_confidence=0.95,
            ),
            SourceMetadata(
                source_id="mca-gov-in",
                name="mca.gov.in",
                display_name="MCA Portal",
                source_type=SourceType.GOVERNMENT,
                authority_tier=1,
                supported_task_types=["MCA_VERIFICATION"],
                base_url="https://www.mca.gov.in",
                priority=1,
                default_confidence=0.95,
            ),
            SourceMetadata(
                source_id="epfindia-gov-in",
                name="epfindia.gov.in",
                display_name="EPFO Portal",
                source_type=SourceType.GOVERNMENT,
                authority_tier=1,
                supported_task_types=["EPFO_VERIFICATION"],
                base_url="https://www.epfindia.gov.in",
                priority=1,
                default_confidence=0.90,
            ),
            SourceMetadata(
                source_id="company-website",
                name="company_website",
                display_name="Company Website",
                source_type=SourceType.OFFICIAL_WEBSITE,
                authority_tier=2,
                supported_task_types=["WEBSITE_VERIFICATION"],
                base_url=None,
                priority=1,
                default_confidence=0.85,
            ),
            SourceMetadata(
                source_id="quickcompany",
                name="quickcompany.in",
                display_name="QuickCompany",
                source_type=SourceType.THIRD_PARTY_REGISTRY,
                authority_tier=3,
                supported_task_types=["MCA_VERIFICATION", "THIRD_PARTY_RESEARCH", "GST_VERIFICATION"],
                base_url="https://www.quickcompany.in",
                priority=2,
                default_confidence=0.80,
                config={
                    "name_url_pattern": "https://www.quickcompany.in/company/{slug}",
                    "cin_url_pattern": "https://www.quickcompany.in/company/{cin}",
                    "gstin_url_pattern": "https://www.quickcompany.in/company/{gstin}",
                    "search_url_pattern": "https://www.quickcompany.in/search?q={query}",
                },
            ),
            SourceMetadata(
                source_id="tofler",
                name="tofler.in",
                display_name="Tofler",
                source_type=SourceType.THIRD_PARTY_REGISTRY,
                authority_tier=3,
                supported_task_types=["MCA_VERIFICATION", "THIRD_PARTY_RESEARCH"],
                base_url="https://www.tofler.in",
                priority=2,
                default_confidence=0.75,
                config={
                    "cin_name_url_pattern": "https://www.tofler.in/{slug}/company/{cin}",
                    "cin_url_pattern": "https://www.tofler.in/company/{cin}",
                    "name_url_pattern": "https://www.tofler.in/company/{slug}",
                    "search_url_pattern": "https://www.tofler.in/search?q={query}",
                },
            ),
            SourceMetadata(
                source_id="zaubacorp",
                name="zaubacorp.com",
                display_name="Zauba Corp",
                source_type=SourceType.THIRD_PARTY_REGISTRY,
                authority_tier=3,
                supported_task_types=["MCA_VERIFICATION", "THIRD_PARTY_RESEARCH"],
                base_url="https://www.zaubacorp.com",
                priority=2,
                default_confidence=0.75,
                config={
                    "cin_name_url_pattern": "https://www.zaubacorp.com/company/{slug}/{cin}",
                    "cin_url_pattern": "https://www.zaubacorp.com/company/{cin}",
                    "name_url_pattern": "https://www.zaubacorp.com/company/{slug}",
                    "search_url_pattern": "https://www.zaubacorp.com/company-list/p-1-company.html?q={query}",
                },
            ),
            SourceMetadata(
                source_id="instafinancials",
                name="instafinancials.com",
                display_name="InstaFinancials",
                source_type=SourceType.THIRD_PARTY_REGISTRY,
                authority_tier=3,
                supported_task_types=["MCA_VERIFICATION", "THIRD_PARTY_RESEARCH"],
                base_url="https://www.instafinancials.com",
                priority=2,
                default_confidence=0.75,
                config={
                    "cin_name_url_pattern": "https://www.instafinancials.com/company/{slug}/{cin}",
                    "cin_url_pattern": "https://www.instafinancials.com/company/{cin}",
                    "name_url_pattern": "https://www.instafinancials.com/company/{slug}",
                    "search_url_pattern": "https://www.instafinancials.com/search?q={query}",
                },
            ),
            SourceMetadata(
                source_id="third-party",
                name="third_party",
                display_name="Third-Party Source",
                source_type=SourceType.THIRD_PARTY_REGISTRY,
                authority_tier=3,
                supported_task_types=["GST_VERIFICATION", "MCA_VERIFICATION", "EPFO_VERIFICATION", "THIRD_PARTY_RESEARCH"],
                base_url="https://www.quickcompany.in",
                priority=3,
                default_confidence=0.50,
                config={
                    "name_url_pattern": "https://www.quickcompany.in/company/{slug}",
                    "cin_url_pattern": "https://www.quickcompany.in/company/{cin}",
                    "gstin_url_pattern": "https://www.quickcompany.in/company/{gstin}",
                    "search_url_pattern": "https://www.quickcompany.in/search?q={query}",
                },
            ),
            SourceMetadata(
                source_id="generic-web",
                name="generic_web",
                display_name="General Web",
                source_type=SourceType.PUBLIC_DIRECTORY,
                authority_tier=4,
                supported_task_types=["ENTITY_DISCOVERY", "GENERAL_WEB_RESEARCH", "WEBSITE_VERIFICATION", "THIRD_PARTY_RESEARCH"],
                base_url="https://www.quickcompany.in",
                priority=4,
                default_confidence=0.60,
                config={
                    "name_url_pattern": "https://www.quickcompany.in/company/{slug}",
                    "cin_url_pattern": "https://www.quickcompany.in/company/{cin}",
                    "gstin_url_pattern": "https://www.quickcompany.in/company/{gstin}",
                    "search_url_pattern": "https://www.quickcompany.in/search?q={query}",
                },
            ),
        ]

        for s in default_sources:
            self.register_source(s)

    def register_source(self, metadata: SourceMetadata):
        self._sources[metadata.name] = metadata
        if metadata.display_name:
            self._sources[metadata.display_name] = metadata
        if metadata.source_id:
            self._sources[metadata.source_id] = metadata

    def get_source(self, name_or_id: str) -> Optional[SourceMetadata]:
        if not name_or_id:
            return None
        if name_or_id in self._sources:
            return self._sources[name_or_id]
        clean_key = name_or_id.lower().replace("_", "-").replace(".", "-")
        for k, v in self._sources.items():
            if k.lower() == name_or_id.lower():
                return v
            if k.lower().replace("_", "-").replace(".", "-") == clean_key:
                return v
        return None

    def list_sources(
        self,
        task_type: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[SourceMetadata]:
        seen = set()
        result = []
        for s in self._sources.values():
            if s.source_id in seen:
                continue
            if enabled_only and not s.enabled:
                continue
            if task_type and task_type not in s.supported_task_types:
                continue
            seen.add(s.source_id)
            result.append(s)
        return sorted(result, key=lambda x: (x.priority, x.authority_tier, x.name))

    def get_preferred_and_fallback_sources(self, task_type: str) -> Tuple[List[str], List[str]]:
        sources = self.list_sources(task_type=task_type, enabled_only=True)
        if not sources:
            if task_type == "ENTITY_DISCOVERY":
                return ["generic_web"], []
            elif task_type == "GST_VERIFICATION":
                return ["gst.gov.in"], ["third_party"]
            elif task_type == "MCA_VERIFICATION":
                return ["mca.gov.in"], ["third_party"]
            elif task_type == "EPFO_VERIFICATION":
                return ["epfindia.gov.in"], ["third_party"]
            elif task_type == "WEBSITE_VERIFICATION":
                return ["company_website"], ["generic_web"]
            return [], []

        names = [s.name for s in sources]
        return [names[0]], names[1:]


source_registry = SourceRegistryManager()
