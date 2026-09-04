"""
Regression: verbose directory/registry page <title> strings that append
registration / identifier / age / date metadata to the entity name must be
parsed down to the actual legal name.

  "<NAME> HAVING CIN <cin> IS 45 YEARS, 2 MONTHS & 2 DAYS OLD"  -> "<NAME>"

Generic structural rules only -- no source or company names. Legitimate legal
names (including ones that end in "INCORPORATED" or start with "Registered")
must be preserved unchanged, and nothing is fabricated when the title carries
no usable name.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest

from app.research.base import clean_legal_name_candidate, is_valid_legal_name
from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask


VERBOSE_TITLES = [
    ("ACME WIDGETS LIMITED HAVING CIN U11111DL2001PLC000001 IS 45 YEARS, 2 MONTHS & 2 DAYS OLD",
     "ACME WIDGETS LIMITED"),
    ("Northwind Trading Private Limited CIN: U63030DL2011PTC221145 | Company Details",
     "Northwind Trading Private Limited"),
    ("Orion Technologies Private Limited HAVING GSTIN 07AABCN1234M1Z8",
     "Orion Technologies Private Limited"),
    ("Blue Ocean Foods Pvt Ltd is 7 years 3 months old",
     "Blue Ocean Foods Pvt Ltd"),
    ("Globex Corporation incorporated on 12-03-1999",
     "Globex Corporation"),
    ("Zenith Enterprises Limited - Date of Incorporation: 01/04/2011",
     "Zenith Enterprises Limited"),
    ("Apex Engineering LLP U74999KA2015PLC000000 Company Profile",
     "Apex Engineering LLP"),
    ("Sierra Metals Limited registered on 5 May 2003",
     "Sierra Metals Limited"),
]

LEGIT_NAMES = [
    "Tata Consultancy Services Limited",
    "INFOSYS LIMITED",
    "Larsen & Toubro Limited",
    "3M India Limited",
    "ACME WIDGETS INCORPORATED",          # bare "Incorporated" suffix must survive
    "Registered Valuers Foundation",       # leading "Registered" must survive
    "General Electric Company",
    "H D F C Bank Limited",
    "Apex Software Solutions LLP",
    "Reliance Industries Limited",
]


@pytest.mark.parametrize("raw,expected", VERBOSE_TITLES)
def test_clean_legal_name_strips_registration_metadata(raw, expected):
    assert clean_legal_name_candidate(raw) == expected


@pytest.mark.parametrize("name", LEGIT_NAMES)
def test_clean_legal_name_preserves_legitimate_names(name):
    assert clean_legal_name_candidate(name) == name


@pytest.mark.parametrize("raw,_expected", VERBOSE_TITLES)
def test_is_valid_legal_name_rejects_uncleaned_verbose_titles(raw, _expected):
    # The raw metadata-laden string must not pass validation directly.
    assert is_valid_legal_name(raw) is False


@pytest.mark.parametrize("name", LEGIT_NAMES)
def test_is_valid_legal_name_accepts_legitimate_names(name):
    assert is_valid_legal_name(name) is True


def test_no_fabrication_when_title_is_only_metadata():
    # A title that is nothing but registration metadata leaves no usable name.
    for junk in (
        "HAVING CIN U11111DL2001PLC000001 IS 12 YEARS OLD",
        "CIN: U11111DL2001PLC000001",
        "U11111DL2001PLC000001",
        "Date of Incorporation: 01/04/2011",
    ):
        assert clean_legal_name_candidate(junk) is None


def test_browser_extractor_uses_clean_name_from_verbose_title():
    """End-to-end through the shared extractor: a THIRD_PARTY page whose only
    name signal is a verbose title yields the clean legal name."""
    title = "ACME WIDGETS LIMITED HAVING CIN U11111DL2001PLC000001 IS 45 YEARS, 2 MONTHS & 2 DAYS OLD"
    page_data = {
        "title": title,
        "text": f"{title}\nCompany Status: Active\nRegistered Address: 5 Industrial Area, New Delhi 110020",
        "url": "https://www.example-registry.test/company/acme-widgets",
    }
    task = ResearchTask(
        task_id="T1", task_type="THIRD_PARTY_RESEARCH",
        target="ACME WIDGETS LIMITED U11111DL2001PLC000001",
        objective="x", required_fields=["legal_name"], priority=2,
        preferred_sources=["third_party"], fallback_sources=[],
    )
    val, _basis = BrowserResearchAgent._extract_field_value_with_basis(task, "legal_name", page_data)
    assert val == "ACME WIDGETS LIMITED"


def test_verbose_title_no_longer_conflicts_with_clean_name():
    """The reconciliation-relevant check: the cleaned verbose-title name matches
    a clean legal name from another source (no false LEGAL_NAME_CONFLICT)."""
    from app.risk.rules import normalize_name
    verbose = clean_legal_name_candidate(
        "INFOSYS LIMITED HAVING CIN L85110KA1981PLC013115 IS 45 YEARS, 2 MONTHS & 2 DAYS OLD"
    )
    clean = "Infosys Limited"
    assert normalize_name(str(verbose)) == normalize_name(clean)
