import re
from app.entity_resolution.normalization import normalize_entity, normalize_text

INCOMPATIBLE_SECTOR_KEYWORDS = {
    "pharma", "pharmaceutical", "hotel", "resorts", "resort", "hospital",
    "motors", "motor", "school", "academy", "coaching", "huel", "adult",
    "spa", "casino", "cinema", "theatre", "restaurant", "cafe", "bar",
}

LEGAL_SUFFIXES = {
    "PVT", "LTD", "LIMITED", "PRIVATE", "LLP", "CORP", "CORPORATION",
    "INC", "INCORPORATED", "CO", "COMPANY", "AND", "THE", "OFFICIAL",
    "WEBSITE", "REGISTRATION", "ESTABLISHMENT", "SEARCH", "PORTAL",
}


def _tokenize_name(name: str | None) -> list[str]:
    if not name:
        return []
    words = re.findall(r"\b[A-Z0-9]+\b", str(name).upper())
    return [w for w in words if w not in LEGAL_SUFFIXES and len(w) > 1]


def compute_name_similarity(target_name: str | None, candidate_name: str | None) -> float:
    if not target_name or not candidate_name:
        return 0.0

    target_tokens = _tokenize_name(target_name)
    candidate_tokens = _tokenize_name(candidate_name)

    if not target_tokens or not candidate_tokens:
        return 0.0

    # Strict exact match
    if " ".join(target_tokens) == " ".join(candidate_tokens):
        return 1.0

    # Incompatible sector check
    target_lower = target_name.lower()
    cand_lower = candidate_name.lower()
    for kw in INCOMPATIBLE_SECTOR_KEYWORDS:
        if kw in cand_lower and kw not in target_lower:
            return 0.0
        if kw in target_lower and kw not in cand_lower:
            return 0.0

    target_set = set(target_tokens)
    candidate_set = set(candidate_tokens)

    overlap = target_set.intersection(candidate_set)
    if not overlap:
        return 0.0

    # If target has numbers or distinctive tokens (e.g. "5842"), check if candidate has them
    target_numbers = [t for t in target_tokens if any(c.isdigit() for c in t)]
    if target_numbers:
        for num in target_numbers:
            if num in candidate_set:
                overlap.add(num)

    # Token overlap ratio relative to target size
    recall = len(overlap) / len(target_set)
    precision = len(overlap) / len(candidate_set)
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    # Single-token overlap when target has multiple tokens is weak
    if len(target_tokens) > 1 and len(overlap) == 1:
        return round(min(0.30, f1), 4)

    return round(f1, 4)


FIELD_WEIGHTS = {
    "name": 0.40,
    "location": 0.15,
    "address": 0.15,
    "website": 0.10,
    "gstin": 0.10,
    "cin": 0.10,
}


def score_entities(
    target: dict,
    candidate: dict,
) -> float:
    normalized_target = normalize_entity(target)
    normalized_candidate = normalize_entity(candidate)

    # 1. Strict Conflict Checks
    for identifier in ["gstin", "cin", "epfo_code"]:
        t_val = normalized_target.get(identifier)
        c_val = normalized_candidate.get(identifier)
        if t_val and c_val and t_val != c_val:
            return 0.0

    target_name = normalized_target.get("name") or normalized_target.get("business_name") or ""
    candidate_name = normalized_candidate.get("name") or normalized_candidate.get("business_name") or ""

    target_lower = target_name.lower()
    cand_lower = candidate_name.lower()
    for kw in INCOMPATIBLE_SECTOR_KEYWORDS:
        if kw in cand_lower and kw not in target_lower:
            return 0.0
        if kw in target_lower and kw not in cand_lower:
            return 0.0

    score = 0.0
    available_weight = 0.0

    for field, weight in FIELD_WEIGHTS.items():
        target_value = normalized_target.get(field)
        candidate_value = normalized_candidate.get(field)

        if not target_value or not candidate_value:
            continue

        available_weight += weight

        if field == "name":
            if target_value == candidate_value:
                score += weight
            else:
                name_sim = compute_name_similarity(target_value, candidate_value)
                score += weight * name_sim
        else:
            if target_value == candidate_value:
                score += weight

    if available_weight == 0:
        return 0.0

    return round(score / available_weight, 4)


def score_candidates(
    target: dict,
    candidates: list[dict],
) -> list[tuple[dict, float]]:
    return [
        (
            candidate,
            score_entities(target, candidate),
        )
        for candidate in candidates
    ]
