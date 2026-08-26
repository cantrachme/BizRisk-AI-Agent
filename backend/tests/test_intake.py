from app.agents.intake import IntakeAgent


def test_intake_normalizes_valid_input():
    result = IntakeAgent().process(
        {
            "business_name": "  ABC   Foods Pvt Ltd ",
            "gstin": "27abcde1234f1z5",
            "website": "ABCFOODS.IN",
            "location": "  Noida ",
        }
    )

    assert result["business_name"] == "ABC FOODS PVT LTD"
    assert result["gstin"] == "27ABCDE1234F1Z5"
    assert result["website"] == "https://abcfoods.in"
    assert result["location"] == "Noida"


def test_intake_keeps_investigation_usable_with_malformed_identifiers():
    result = IntakeAgent().process(
        {
            "business_name": "ABC Foods",
            "gstin": "invalid",
            "cin": "invalid",
        }
    )

    assert result["business_name"] == "ABC FOODS"
    assert result["gstin"] is None
    assert result["cin"] is None


def test_intake_accepts_partial_input():
    result = IntakeAgent().process(
        {
            "website": "example.com",
        }
    )

    assert result["business_name"] is None
    assert result["website"] == "https://example.com"
