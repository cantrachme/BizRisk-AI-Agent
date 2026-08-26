import re
from urllib.parse import urlparse


GSTIN_PATTERN = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$"
)

CIN_PATTERN = re.compile(
    r"^[A-Z][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$"
)


class IntakeAgent:
    def process(self, data: dict) -> dict:
        return {
            "business_name": self._normalize_name(data.get("business_name")),
            "gstin": self._normalize_gstin(data.get("gstin")),
            "cin": self._normalize_cin(data.get("cin")),
            "website": self._normalize_website(data.get("website")),
            "location": self._normalize_text(data.get("location")),
            "people": data.get("people", []),
        }

    @staticmethod
    def _normalize_text(value: str | None) -> str | None:
        if not value:
            return None

        value = " ".join(value.split())
        return value or None

    def _normalize_name(self, value: str | None) -> str | None:
        value = self._normalize_text(value)
        return value.upper() if value else None

    @staticmethod
    def _normalize_gstin(value: str | None) -> str | None:
        if not value:
            return None

        value = value.strip().upper()
        return value if GSTIN_PATTERN.fullmatch(value) else None

    @staticmethod
    def _normalize_cin(value: str | None) -> str | None:
        if not value:
            return None

        value = value.strip().upper()
        return value if CIN_PATTERN.fullmatch(value) else None

    @staticmethod
    def _normalize_website(value: str | None) -> str | None:
        if not value:
            return None

        value = value.strip().lower()

        if "://" not in value:
            value = f"https://{value}"

        parsed = urlparse(value)

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None

        return value
