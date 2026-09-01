from __future__ import annotations

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
            ),
            SourceMetadata(
                source_id="third-party",
                name="third_party",
                display_name="Third-Party Source",
                source_type=SourceType.THIRD_PARTY_REGISTRY,
                authority_tier=3,
                supported_task_types=["GST_VERIFICATION", "MCA_VERIFICATION", "EPFO_VERIFICATION", "THIRD_PARTY_RESEARCH"],
                base_url=None,
                priority=3,
                default_confidence=0.50,
            ),
            SourceMetadata(
                source_id="generic-web",
                name="generic_web",
                display_name="General Web",
                source_type=SourceType.PUBLIC_DIRECTORY,
                authority_tier=4,
                supported_task_types=["ENTITY_DISCOVERY", "GENERAL_WEB_RESEARCH", "WEBSITE_VERIFICATION", "THIRD_PARTY_RESEARCH"],
                base_url=None,
                priority=4,
                default_confidence=0.60,
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
