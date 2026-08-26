import re
from urllib.parse import urlparse


def normalize_text(value: str | None) -> str | None:
    if not value:
        return None

    value = " ".join(value.split())
    return value.upper() or None


def normalize_identifier(value: str | None) -> str | None:
    if not value:
        return None

    value = re.sub(r"\s+", "", value)
    return value.upper() or None


def normalize_website(value: str | None) -> str | None:
    if not value:
        return None

    value = value.strip().lower()

    if "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    hostname = parsed.hostname

    if not hostname:
        return None

    hostname = hostname.removeprefix("www.")

    if (
        " " in hostname
        or "." not in hostname
        or hostname.startswith(".")
        or hostname.endswith(".")
        or not re.fullmatch(r"[a-z0-9.-]+", hostname)
    ):
        return None

    return hostname


def normalize_entity(entity: dict) -> dict:
    return {
        "name": normalize_text(entity.get("name")),
        "business_name": normalize_text(entity.get("business_name")),
        "gstin": normalize_identifier(entity.get("gstin")),
        "cin": normalize_identifier(entity.get("cin")),
        "website": normalize_website(entity.get("website")),
        "location": normalize_text(entity.get("location")),
        "address": normalize_text(entity.get("address")),
    }
