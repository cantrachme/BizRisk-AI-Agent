from app.entity_resolution.matcher import has_exact_match
from app.entity_resolution.scoring import score_entities, _tokenize_name
from app.entity_resolution.normalization import normalize_entity

EXACT_MATCH_CONFIDENCE = 1.0
# Default acceptance threshold. The effective value is read from
# Settings.entity_resolution_threshold at call time so it stays configurable.
RESOLUTION_THRESHOLD = 0.75

# Statutory identifiers whose disagreement means "different legal entity".
_STRONG_IDENTIFIERS = ("gstin", "cin")
# Non-name identity attributes that can corroborate a name-based similarity match.
_CORROBORATING_ATTRS = ("gstin", "cin", "website", "location", "address")


def _resolution_threshold() -> float:
    try:
        from app.core.config import get_settings

        return float(get_settings().entity_resolution_threshold)
    except Exception:
        return RESOLUTION_THRESHOLD


def _conflicts_on_strong_identifier(normalized_target: dict, candidate: dict) -> bool:
    """True when target and candidate carry the same kind of statutory
    identifier (GSTIN/CIN) with different values."""
    nc = normalize_entity(candidate)
    return any(
        normalized_target.get(k) and nc.get(k) and normalized_target[k] != nc[k]
        for k in _STRONG_IDENTIFIERS
    )


def resolve_entity(
    target: dict,
    candidates: list[dict],
    llm=None,
    prompt_version: str = "v1",
) -> dict:
    resolution_threshold = _resolution_threshold()
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

    # GAP A: a matching identifier (incl. website) must never override a
    # *conflicting* statutory identifier on the same candidate. Such candidates
    # are not eligible for an EXACT/MATCH; the investigation falls through to the
    # CONFLICTING_IDENTITY path below.
    exact_matches = [
        candidate
        for candidate in exact_matches
        if not _conflicts_on_strong_identifier(normalized_target, candidate)
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

    if best_score >= resolution_threshold:
        # GAP B: a similarity match must not rest on a single non-distinctive
        # name token when the name is the only identity signal (no other
        # corroborating identity attribute agrees). Reuses the same name
        # tokeniser as scoring; no company-specific lists.
        norm_best = normalize_entity(best_candidate)
        has_corroboration = any(
            normalized_target.get(k) and norm_best.get(k) and normalized_target[k] == norm_best[k]
            for k in _CORROBORATING_ATTRS
        )
        target_name = normalized_target.get("name") or normalized_target.get("business_name") or ""
        if not has_corroboration and len(_tokenize_name(target_name)) < 2:
            # `confidence` carries the best candidate's raw similarity (as the
            # sibling NO_MATCH branch does); `matched` / `match_type` are the
            # signal that identity could not be resolved.
            return {
                "entity": None,
                "confidence": best_score,
                "matched": False,
                "match_type": "INSUFFICIENT_IDENTITY",
                "resolution_status": "ENTITY_UNRESOLVED",
                "match_reasons": [
                    "Name is the only identity signal and is not distinctive enough "
                    "to resolve the entity without corroborating attributes."
                ],
                "conflicting_identifiers": conflicts,
            }

        reasons = [f"Multi-attribute similarity score {best_score:.2f} >= threshold {resolution_threshold}"]
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
        "match_reasons": [f"Best candidate match score {best_score:.2f} below threshold {resolution_threshold}"],
        "conflicting_identifiers": conflicts,
    }
