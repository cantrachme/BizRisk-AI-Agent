from app.entity_resolution.resolver import resolve_entity


def test_no_candidates_returns_no_match():
    result = resolve_entity(
        {"name": "ABC Foods"},
        [],
    )

    assert result["entity"] is None
    assert result["confidence"] == 0.0
    assert result["matched"] is False
    assert result["match_type"] == "NO_MATCH"


def test_exact_match_takes_priority():
    target = {
        "name": "ABC Foods Pvt Ltd",
        "gstin": "27ABCDE1234F1Z5",
    }

    candidates = [
        {
            "name": "ABC Foods Similar",
        },
        {
            "name": "Completely Different Name",
            "gstin": "27abcde1234f1z5",
        },
    ]

    result = resolve_entity(target, candidates)

    assert result["entity"] == candidates[1]
    assert result["confidence"] == 1.0
    assert result["matched"] is True
    assert result["match_type"] == "EXACT"


def test_similarity_match_above_threshold():
    target = {
        "name": "ABC Foods Pvt Ltd",
        "location": "Noida",
        "website": "abcfoods.in",
    }

    candidate = {
        "name": "abc foods pvt ltd",
        "location": "NOIDA",
        "website": "https://www.abcfoods.in/about",
    }

    result = resolve_entity(target, [candidate])

    assert result["entity"] == candidate
    assert result["confidence"] == 1.0
    assert result["matched"] is True
    assert result["match_type"] == "EXACT"


def test_low_similarity_returns_no_match():
    target = {
        "name": "ABC Foods",
        "location": "Noida",
    }

    candidate = {
        "name": "Completely Different Company",
        "location": "Delhi",
    }

    result = resolve_entity(target, [candidate])

    assert result["entity"] == candidate
    assert result["confidence"] == 0.0
    assert result["matched"] is False
    assert result["match_type"] == "NO_MATCH"


def test_best_scoring_candidate_is_selected():
    target = {
        "name": "ABC Foods Pvt Ltd",
        "location": "Noida",
    }

    candidates = [
        {
            "name": "Different Company",
            "location": "Mumbai",
        },
        {
            "name": "abc foods pvt ltd",
            "location": "Noida",
        },
        {
            "name": "ABC Foods Pvt Ltd",
            "location": "Delhi",
        },
    ]

    result = resolve_entity(target, candidates)

    assert result["entity"] == candidates[1]
    assert result["confidence"] == 1.0
    assert result["matched"] is True
    assert result["match_type"] == "SIMILARITY"
