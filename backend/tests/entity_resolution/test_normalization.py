from app.entity_resolution.normalization import (
    normalize_text,
    normalize_identifier,
    normalize_website,
    normalize_entity,
)


def test_normalize_text():
    assert normalize_text("  ABC   Foods Pvt Ltd  ") == "ABC FOODS PVT LTD"


def test_normalize_text_returns_none_for_empty_value():
    assert normalize_text("   ") is None
    assert normalize_text(None) is None


def test_normalize_identifier():
    assert (
        normalize_identifier(" 27abcde1234f1z5 ")
        == "27ABCDE1234F1Z5"
    )


def test_normalize_website():
    assert normalize_website("https://www.abcfoods.in/about") == "abcfoods.in"
    assert normalize_website("abcfoods.in") == "abcfoods.in"


def test_normalize_invalid_website():
    assert normalize_website("not a valid website") is None
    assert normalize_website(None) is None


def test_normalize_entity():
    entity = {
        "name": "  ABC Foods Pvt Ltd ",
        "gstin": " 27abcde1234f1z5 ",
        "cin": " u12345mh2020ptc123456 ",
        "website": "https://www.abcfoods.in",
        "location": "  Noida ",
        "address": "  Sector   62, Noida ",
    }

    normalized = normalize_entity(entity)

    assert normalized["name"] == "ABC FOODS PVT LTD"
    assert normalized["gstin"] == "27ABCDE1234F1Z5"
    assert normalized["cin"] == "U12345MH2020PTC123456"
    assert normalized["website"] == "abcfoods.in"
    assert normalized["location"] == "NOIDA"
    assert normalized["address"] == "SECTOR 62, NOIDA"
