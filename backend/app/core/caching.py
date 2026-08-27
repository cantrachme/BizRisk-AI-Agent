import uuid
from typing import Dict, Any, Optional


class ResolvedEntityCache:
    _cache: Dict[tuple, Dict[str, Any]] = {}

    @classmethod
    def _make_key(cls, investigation_id: str | uuid.UUID, target: dict) -> tuple:
        inv_str = str(investigation_id)
        name = target.get("business_name") or target.get("name") or ""
        gstin = target.get("gstin") or ""
        cin = target.get("cin") or ""

        # Normalize
        norm_name = str(name).strip().lower()
        norm_gstin = str(gstin).strip().upper()
        norm_cin = str(cin).strip().upper()

        return (inv_str, norm_name, norm_gstin, norm_cin)

    @classmethod
    def get(cls, investigation_id: str | uuid.UUID, target: dict) -> Optional[Dict[str, Any]]:
        key = cls._make_key(investigation_id, target)
        return cls._cache.get(key)

    @classmethod
    def set(cls, investigation_id: str | uuid.UUID, target: dict, resolution: Dict[str, Any]) -> None:
        key = cls._make_key(investigation_id, target)
        cls._cache[key] = resolution

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()


class NormalizedNameCache:
    _cache: Dict[tuple, Any] = {}

    @classmethod
    def get(cls, name_type: str, value: str | dict | None) -> Any:
        if value is None:
            return None
        # Stringify input to make it hashable
        key = (name_type, str(value))
        return cls._cache.get(key)

    @classmethod
    def has(cls, name_type: str, value: str | dict | None) -> bool:
        if value is None:
            return False
        key = (name_type, str(value))
        return key in cls._cache

    @classmethod
    def set(cls, name_type: str, value: str | dict | None, normalized: Any) -> None:
        if value is None:
            return
        key = (name_type, str(value))
        cls._cache[key] = normalized

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()
