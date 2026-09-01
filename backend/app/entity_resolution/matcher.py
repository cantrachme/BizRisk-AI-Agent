from app.entity_resolution.normalization import normalize_entity

IDENTIFIER_FIELDS = (
    "gstin",
    "cin",
    "website",
)


def find_exact_matches(
    target: dict,
    candidates: list[dict],
) -> list[dict]:
    normalized_target = normalize_entity(target)
    matches = []

    for candidate in candidates:
        normalized_candidate = normalize_entity(candidate)

        for field in IDENTIFIER_FIELDS:
            target_value = normalized_target.get(field)
            candidate_value = normalized_candidate.get(field)

            if (
                target_value
                and candidate_value
                and target_value == candidate_value
            ):
                matches.append(candidate)
                break

    return matches


def has_exact_match(
    target: dict,
    candidate: dict,
) -> bool:
    return bool(
        find_exact_matches(
            target,
            [candidate],
        )
    )
