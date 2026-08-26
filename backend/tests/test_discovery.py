from app.agents.discovery import DiscoveryAgent


def test_discovery_creates_candidate_from_gstin():
    result = DiscoveryAgent().process(
        {
            "business_name": "ABC FOODS PRIVATE LIMITED",
            "gstin": "27ABCDE1234F1Z5",
            "cin": None,
            "website": None,
            "location": None,
        }
    )

    candidate = result["candidate_entities"][0]

    assert candidate["name"] == "ABC FOODS PRIVATE LIMITED"
    assert candidate["gstin"] == "27ABCDE1234F1Z5"
    assert candidate["confidence"] == 0.95


def test_discovery_uses_partial_identity_information():
    result = DiscoveryAgent().process(
        {
            "business_name": "ABC FOODS",
            "website": "https://abcfoods.in",
            "location": "Noida",
        }
    )

    candidate = result["candidate_entities"][0]

    assert candidate["name"] == "ABC FOODS"
    assert candidate["website"] == "https://abcfoods.in"
    assert candidate["location"] == "Noida"
    assert candidate["confidence"] == 0.80


def test_discovery_returns_no_candidates_without_input():
    result = DiscoveryAgent().process({})

    assert result == {"candidate_entities": []}
