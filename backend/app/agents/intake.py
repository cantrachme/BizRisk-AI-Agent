import re
from urllib.parse import urlparse


GSTIN_PATTERN = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$"
)

CIN_PATTERN = re.compile(
    r"^[A-Z][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$"
)


class IntakeAgent:
    def __init__(self, llm=None, prompt_version: str = "v1"):
        from app.core.llm import get_llm_provider
        from app.core.prompts import load_prompt
        self.llm = llm or get_llm_provider(temperature=0.0)
        self.prompt_version = prompt_version
        self.prompt = load_prompt("intake", prompt_version)

    def process(self, data: dict) -> dict:
        raw_biz_name = data.get("business_name")
        raw_gstin = data.get("gstin")
        raw_cin = data.get("cin")
        raw_epfo = data.get("epfo_code")
        raw_web = data.get("website")
        raw_loc = data.get("location")

        norm_biz_name = self._normalize_name(raw_biz_name)
        norm_gstin, gstin_prov = self._process_gstin(raw_gstin)
        norm_cin, cin_prov = self._process_cin(raw_cin)
        norm_epfo, epfo_prov = self._process_text(raw_epfo)
        norm_web, web_prov = self._process_website(raw_web)
        norm_loc, loc_prov = self._process_text(raw_loc)

        provenance = {
            "business_name": "USER_SUPPLIED" if norm_biz_name else "NOT_CHECKED",
            "gstin": gstin_prov,
            "cin": cin_prov,
            "epfo_code": epfo_prov,
            "website": web_prov,
            "location": loc_prov,
        }

        return {
            "business_name": norm_biz_name,
            "gstin": norm_gstin,
            "cin": norm_cin,
            "epfo_code": norm_epfo,
            "website": norm_web,
            "location": norm_loc,
            "people": data.get("people", []),
            "identifier_provenance": provenance,
        }

    @staticmethod
    def _process_text(value: str | None) -> tuple[str | None, str]:
        if not value or not str(value).strip():
            return None, "NOT_CHECKED"
        cleaned = " ".join(str(value).split())
        return cleaned or None, "USER_SUPPLIED"

    @staticmethod
    def _process_gstin(value: str | None) -> tuple[str | None, str]:
        if not value or not str(value).strip():
            return None, "NOT_CHECKED"
        cleaned = str(value).strip().upper()
        if re.match(r"^[0-9]{2}[A-Z0-9]{12,13}$", cleaned):
            return cleaned, "USER_SUPPLIED"
        return None, "INVALID"

    @staticmethod
    def _process_cin(value: str | None) -> tuple[str | None, str]:
        if not value or not str(value).strip():
            return None, "NOT_CHECKED"
        cleaned = str(value).strip().upper()
        if re.match(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$", cleaned):
            return cleaned, "USER_SUPPLIED"
        return None, "INVALID"

    @staticmethod
    def _process_website(value: str | None) -> tuple[str | None, str]:
        if not value or not str(value).strip():
            return None, "NOT_CHECKED"
        cleaned = str(value).strip().lower()
        if "://" not in cleaned:
            cleaned = f"https://{cleaned}"
        try:
            parsed = urlparse(cleaned)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                hostname = parsed.hostname
                if hostname and "." in hostname and re.match(r"^[a-z0-9\-\.:]+$", hostname):
                    return cleaned, "USER_SUPPLIED"
        except Exception:
            pass
        return None, "INVALID"

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

        try:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return None
            
            hostname = parsed.hostname
            if not hostname:
                return None
            
            if not re.match(r"^[a-z0-9\-\.:]+$", hostname):
                return None
            
            if "." not in hostname and hostname != "localhost" and ":" not in hostname:
                return None
        except Exception:
            return None

        return value
