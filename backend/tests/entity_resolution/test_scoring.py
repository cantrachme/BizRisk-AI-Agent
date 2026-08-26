from app.entity_resolution.scoring import (
    score_candidates,
    score_entities,
)


def test_perfect_match_scores_one():
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

    assert score_entities(target, candidate) == 1.0


def test_partial_match_scores_by_available_weights():
    target = {
        "name": "ABC Foods Pvt Ltd",
        "location": "Noida",
    }

    candidate = {
        "name": "ABC Foods Pvt Ltd",
        "location": "Delhi",
    }

    score = score_entities(target, candidate)

    assert score == round(0.40 / 0.55, 4)


def test_normalization_is_applied_before_scoring():
    target = {
        "name": "  ABC   Foods Pvt Ltd ",
        "website": "https://www.abcfoods.in/about",
    }

    candidate = {
        "name": "abc foods pvt ltd",
        "website": "abcfoods.in",
    }

    assert score_entities(target, candidate) == 1.0


def test_no_shared_fields_returns_zero():
    target = {
        "name": "ABC Foods",
    }

    candidate = {
        "location": "Noida",
    }

    assert score_entities(target, candidate) == 0.0


def test_score_candidates_returns_scores_for_all_candidates():
    target = {
        "name": "ABC Foods",
    }

    candidates = [
        {"name": "ABC Foods"},
        {"name": "Different Company"},
    ]

    scored = score_candidates(target, candidates)

    assert len(scored) == 2
    assert scored[0][0]["name"] == "ABC Foods"
    assert scored[0][1] == 1.0
    assert scored[1][1] == 0.0
