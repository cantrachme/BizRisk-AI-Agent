"""
Regression: NIC / ISIC classification-dictionary text must not be accepted as a
business-activity value.

The dictionary form is a short class label followed by a bracketed / parenthesised
"[Includes ...]" / "(Excludes ...)" gloss. That is reference material, not a
company's own line-of-business statement. The rule is purely structural -- no
company names, no phrase blacklist -- so genuine descriptions are preserved.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest

from app.research.base import (
    extract_business_activity_from_text,
    is_valid_business_activity,
)


CLASSIFICATION_GLOSS_VALUES = [
    "Printing [Includes printing of newspapers, books, periodicals and other materials]",
    "Wholesale Trade [Includes wholesale on a fee or contract basis]",
    "Manufacture of paper [Excludes manufacture of paper stationery]",
    "Retail sale (Includes sale of second-hand goods in stores)",
    "Financial service activities (excluding insurance and pension funding)",
    "Printing [ Incl. printing of newspapers ]",
]

GENUINE_ACTIVITY_VALUES = [
    "Manufacture of paper and paper products",
    "Software development services",
    "Retail sale of garments",
    "Wholesale trade",
    "IT services",
    "Manufacture of textiles and wearing apparel",
    "Construction of residential and commercial buildings",
]


@pytest.mark.parametrize("value", CLASSIFICATION_GLOSS_VALUES)
def test_is_valid_business_activity_rejects_classification_gloss(value):
    assert is_valid_business_activity(value) is False


@pytest.mark.parametrize("value", GENUINE_ACTIVITY_VALUES)
def test_is_valid_business_activity_preserves_genuine_descriptions(value):
    assert is_valid_business_activity(value) is True


def test_extract_business_activity_skips_nic_dictionary_line():
    text = (
        "Company Master Data\n"
        "NIC Code Description: Printing [Includes printing of newspapers, books, "
        "periodicals, business forms, greeting cards and other materials]\n"
        "Company Status: Active\n"
    )
    assert extract_business_activity_from_text(text) == "NOT_FOUND"


def test_extract_business_activity_still_reads_real_activity_line():
    text = (
        "Principal Business Activity: Retail sale of garments\n"
        "Company Status: Active\n"
    )
    assert extract_business_activity_from_text(text) == "Retail sale of garments"


def test_extract_business_activity_real_line_with_plain_parenthetical_kept():
    # A parenthetical that is NOT an includes/excludes gloss is still a normal
    # description and must be preserved.
    text = "Business Activity: Manufacture of paper products for packaging (Unit II)\n"
    assert extract_business_activity_from_text(text) == (
        "Manufacture of paper products for packaging (Unit II)"
    )
    assert is_valid_business_activity(
        "Manufacture of paper products for packaging (Unit II)"
    ) is True
