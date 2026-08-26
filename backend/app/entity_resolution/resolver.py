from app.entity_resolution.matcher import has_exact_match
from app.entity_resolution.scoring import score_entities


EXACT_MATCH_CONFIDENCE = 1.0
RESOLUTION_THRESHOLD = 0.75


def resolve_entity(
    target: dict,
    candidates: list[dict],
) -> dict:
    if not candidates:
        return {
            "entity": None,
            "confidence": 0.0,
            "matched": False,
            "match_type": "NO_MATCH",
        }

    exact_matches = [
        candidate
        for candidate in candidates
        if has_exact_match(target, candidate)
    ]

    if exact_matches:
        return {
            "entity": exact_matches[0],
            "confidence": EXACT_MATCH_CONFIDENCE,
            "matched": True,
            "match_type": "EXACT",
        }

    scored_candidates = [
        (
            candidate,
            score_entities(target, candidate),
        )
        for candidate in candidates
    ]

    best_candidate, best_score = max(
        scored_candidates,
        key=lambda item: item[1],
    )

    return {
        "entity": best_candidate,
        "confidence": best_score,
        "matched": best_score >= RESOLUTION_THRESHOLD,
        "match_type": (
            "SIMILARITY"
            if best_score >= RESOLUTION_THRESHOLD
            else "NO_MATCH"
        ),
    }
