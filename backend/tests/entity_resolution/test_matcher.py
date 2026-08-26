from app.entity_resolution.matcher import (
    find_exact_matches,
    has_exact_match,
)


def test_matches_by_gstin():
    target = {
        "name": "ABC Foods Pvt Ltd",
        "gstin": "27ABCDE1234F1Z5",
    }

    candidates = [
        {
            "name": "Different Name",
            "gstin": "27abcde1234f1z5",
        },
        {
            "name": "Other Company",
            "gstin": "09ABCDE1234F1Z5",
        },
    ]

    matches = find_exact_matches(target, candidates)

    assert len(matches) == 1
    assert matches[0]["name"] == "Different Name"


def test_matches_by_cin():
    target = {
        "name": "ABC Foods Pvt Ltd",
        "cin": "U12345MH2020PTC123456",
    }

    candidate = {
        "name": "Another Name",
        "cin": "u12345mh2020ptc123456",
    }

    assert has_exact_match(target, candidate) is True


def test_matches_by_normalized_website():
    target = {
        "website": "https://www.abcfoods.in/about",
    }

    candidate = {
        "website": "abcfoods.in",
    }

    assert has_exact_match(target, candidate) is True


def test_returns_no_match_when_identifiers_differ():
    target = {
        "gstin": "27ABCDE1234F1Z5",
        "cin": "U12345MH2020PTC123456",
        "website": "abcfoods.in",
    }

    candidate = {
        "gstin": "09ABCDE1234F1Z5",
        "cin": "U99999MH2020PTC999999",
        "website": "differentcompany.in",
    }

    assert has_exact_match(target, candidate) is False


def test_find_exact_matches_returns_all_matching_candidates():
    target = {
        "gstin": "27ABCDE1234F1Z5",
    }

    candidates = [
        {
            "name": "ABC Foods",
            "gstin": "27ABCDE1234F1Z5",
        },
        {
            "name": "ABC Foods Duplicate",
            "gstin": "27abcde1234f1z5",
        },
        {
            "name": "Other Company",
            "gstin": "09ABCDE1234F1Z5",
        },
    ]

    matches = find_exact_matches(target, candidates)

    assert len(matches) == 2
