from app.entity_resolution.matcher import has_exact_match
from app.entity_resolution.scoring import score_entities
from app.entity_resolution.normalization import normalize_entity

EXACT_MATCH_CONFIDENCE = 1.0
RESOLUTION_THRESHOLD = 0.75


def resolve_entity(
    target: dict,
    candidates: list[dict],
    llm=None,
    prompt_version: str = "v1",
) -> dict:
    normalized_target = normalize_entity(target) if target else {}

    if not candidates:
        return {
            "entity": None,
            "confidence": 0.0,
            "matched": False,
            "match_type": "NO_MATCH",
            "resolution_status": "ENTITY_UNRESOLVED",
            "match_reasons": [],
            "conflicting_identifiers": [],
        }

    # Check for conflicting identifiers
    conflicts = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        norm_cand = normalize_entity(cand)
        for ident in ["gstin", "cin", "epfo_code"]:
            t_val = normalized_target.get(ident)
            c_val = norm_cand.get(ident)
            if t_val and c_val and t_val != c_val:
                conflicts.append({
                    "field": ident,
                    "target_value": t_val,
                    "candidate_value": c_val,
                    "candidate_name": norm_cand.get("name") or norm_cand.get("business_name"),
                })

    exact_matches = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and has_exact_match(target, candidate)
    ]

    if exact_matches:
        matched_cand = exact_matches[0]
        reasons = ["Exact unique identifier match"]
        if matched_cand.get("gstin") and normalized_target.get("gstin") == matched_cand.get("gstin"):
            reasons.append("Exact GSTIN match")
        if matched_cand.get("cin") and normalized_target.get("cin") == matched_cand.get("cin"):
            reasons.append("Exact CIN match")

        return {
            "entity": matched_cand,
            "confidence": EXACT_MATCH_CONFIDENCE,
            "matched": True,
            "match_type": "EXACT",
            "resolution_status": "RESOLVED",
            "match_reasons": reasons,
            "conflicting_identifiers": conflicts,
        }

    scored_candidates = [
        (candidate, score_entities(target, candidate))
        for candidate in candidates
        if isinstance(candidate, dict)
    ]

    if not scored_candidates:
        return {
            "entity": None,
            "confidence": 0.0,
            "matched": False,
            "match_type": "NO_MATCH",
            "resolution_status": "ENTITY_UNRESOLVED",
            "match_reasons": [],
            "conflicting_identifiers": conflicts,
        }

    best_candidate, best_score = max(
        scored_candidates,
        key=lambda item: item[1],
    )

    if best_score >= RESOLUTION_THRESHOLD:
        reasons = [f"Multi-attribute similarity score {best_score:.2f} >= threshold {RESOLUTION_THRESHOLD}"]
        return {
            "entity": best_candidate,
            "confidence": best_score,
            "matched": True,
            "match_type": "SIMILARITY",
            "resolution_status": "RESOLVED",
            "match_reasons": reasons,
            "conflicting_identifiers": conflicts,
        }

    if conflicts:
        return {
            "entity": None,
            "confidence": 0.0,
            "matched": False,
            "match_type": "CONFLICTING_IDENTITY",
            "resolution_status": "CONFLICTING_IDENTITY",
            "match_reasons": ["Identifier conflict detected with all evaluated candidate records."],
            "conflicting_identifiers": conflicts,
        }

    return {
        "entity": best_candidate,
        "confidence": best_score,
        "matched": False,
        "match_type": "NO_MATCH",
        "resolution_status": "ENTITY_UNRESOLVED",
        "match_reasons": [f"Best candidate match score {best_score:.2f} below threshold {RESOLUTION_THRESHOLD}"],
        "conflicting_identifiers": conflicts,
    }
