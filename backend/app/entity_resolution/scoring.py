from app.entity_resolution.normalization import normalize_entity


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

    score = 0.0
    available_weight = 0.0

    for field, weight in FIELD_WEIGHTS.items():
        target_value = normalized_target.get(field)
        candidate_value = normalized_candidate.get(field)

        if not target_value or not candidate_value:
            continue

        available_weight += weight

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
