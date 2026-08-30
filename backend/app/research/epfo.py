from __future__ import annotations

from typing import Any, Dict


class EpfoResearchProvider:
    """
    Research provider for EPFO (Employees' Provident Fund Organisation) verification.
    """

    SOURCE_NAME = "EPFO Portal"
    SOURCE_URL = "https://www.epfindia.gov.in"
    DEFAULT_CONFIDENCE = 0.90

    @staticmethod
    def extract_epfo_data(html: str | None, epfo_code_or_target: str) -> Dict[str, Any]:
        """
        Extract EPFO establishment details from page content or fallback structure.
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
