import re
from urllib.parse import urlparse


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None

    from app.core.caching import NormalizedNameCache
    if NormalizedNameCache.has("text", value):
        return NormalizedNameCache.get("text", value)

    if not value:
        res = None
    else:
        res = " ".join(value.split()).upper() or None

    NormalizedNameCache.set("text", value, res)
    return res


def normalize_identifier(value: str | None) -> str | None:
    if value is None:
        return None

    from app.core.caching import NormalizedNameCache
    if NormalizedNameCache.has("identifier", value):
        return NormalizedNameCache.get("identifier", value)

    if not value:
        res = None
    else:
        res = re.sub(r"\s+", "", value).upper() or None

    NormalizedNameCache.set("identifier", value, res)
    return res


def normalize_website(value: str | None) -> str | None:
    if value is None:
        return None

    from app.core.caching import NormalizedNameCache
    if NormalizedNameCache.has("website", value):
        return NormalizedNameCache.get("website", value)

    if not value:
        res = None
    else:
        val_stripped = value.strip().lower()
        if "://" not in val_stripped:
            val_stripped = f"https://{val_stripped}"

        parsed = urlparse(val_stripped)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            res = None
        else:
            hostname = parsed.hostname
            if not hostname:
                res = None
            else:
                hostname = hostname.removeprefix("www.")
                if (
                    " " in hostname
                    or "." not in hostname
                    or hostname.startswith(".")
                    or hostname.endswith(".")
                    or not re.fullmatch(r"[a-z0-9.-]+", hostname)
                ):
                    res = None
                else:
                    res = hostname

    NormalizedNameCache.set("website", value, res)
    return res


def normalize_entity(entity: dict) -> dict:
    if entity is None:
        return {}

    from app.core.caching import NormalizedNameCache
    if NormalizedNameCache.has("entity", entity):
        return NormalizedNameCache.get("entity", entity)

    name_val = entity.get("name") or entity.get("business_name")
    biz_name_val = entity.get("business_name") or entity.get("name")

    res = {
        "name": normalize_text(name_val),
        "business_name": normalize_text(biz_name_val),
        "gstin": normalize_identifier(entity.get("gstin")),
        "cin": normalize_identifier(entity.get("cin")),
        "website": normalize_website(entity.get("website")),
        "location": normalize_text(entity.get("location")),
        "address": normalize_text(entity.get("address")),
    }

    NormalizedNameCache.set("entity", entity, res)
    return res
