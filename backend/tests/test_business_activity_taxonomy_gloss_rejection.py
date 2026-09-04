"""
business_activity extraction must keep industry-classification / taxonomy /
menu / gloss text out of the structured field, while accepting legitimate
concise or company-specific activity descriptions -- including ones that
legitimately contain punctuation or parentheses.

Generic and entity-agnostic: the rule keys on the *structural shape* of a
classification gloss (a bracketed / parenthesised enumeration opened by a closed
set of taxonomy connectives such as "includes" / "for example" / "e.g." /
"n.e.c." / "this class covers"), never on a company name or an activity phrase
list.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.graph.state import ResearchResult
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.research.base import (
    extract_business_activity_from_text,
    is_valid_business_activity,
)
from app.services.evidence import save_research_results
from app.validation.research import validate_research_result


# --------------------------------------------------------------------------- #
# 1. bracketed classification / gloss text is rejected
# --------------------------------------------------------------------------- #
BRACKETED_GLOSS = [
    "Printing [Includes printing of newspapers, books, periodicals and other materials]",
    "Wholesale trade [this class includes wholesale on a fee or contract basis]",
    "Manufacture of basic metals [this group covers smelting and refining of metals]",
    "Repair of goods [also includes installation]",
    "Manufacturing [not elsewhere classified]",
    "Construction of buildings [ including residential and non-residential ]",
]


@pytest.mark.parametrize("value", BRACKETED_GLOSS)
def test_1_bracketed_classification_gloss_is_rejected(value):
    assert is_valid_business_activity(value) is False


# --------------------------------------------------------------------------- #
# 2. parenthesised text is rejected only when it is clearly taxonomy
# --------------------------------------------------------------------------- #
PARENTHESISED_TAXONOMY = [
    "Manufacture of paper products (n.e.c.)",
    "Retail sale in stores (e.g. supermarkets, department stores)",
    "Financial service activities (excluding insurance and pension funding)",
    "Growing of crops (such as cereals, leguminous crops and oil seeds)",
]


@pytest.mark.parametrize("value", PARENTHESISED_TAXONOMY)
def test_2_parenthesised_taxonomy_text_is_rejected(value):
    assert is_valid_business_activity(value) is False


# --------------------------------------------------------------------------- #
# 3. includes / including / excludes / excluding glosses are rejected
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kw", ["Includes", "including", "Excludes", "excluding", "incl.", "excl."])
def test_3_include_exclude_gloss_keywords_are_rejected(kw):
    assert is_valid_business_activity(f"Some industry class [{kw} a long enumeration of sub-activities]") is False
    assert is_valid_business_activity(f"Some industry class ({kw} a long enumeration of sub-activities)") is False


# --------------------------------------------------------------------------- #
# 4. legitimate concise business activities are accepted
# --------------------------------------------------------------------------- #
LEGIT_CONCISE = [
    "Manufacture of paper and paper products",
    "Software development services",
    "Retail sale of garments",
    "Wholesale trade",
    "IT services",
    "Freight transport by road",
    "Construction of residential and commercial buildings",
    "Manufacture of textiles and wearing apparel",
]


@pytest.mark.parametrize("value", LEGIT_CONCISE)
def test_4_legitimate_concise_activities_are_accepted(value):
    assert is_valid_business_activity(value) is True


# --------------------------------------------------------------------------- #
# 5. legitimate company-specific activities with parentheses are accepted
# --------------------------------------------------------------------------- #
LEGIT_PARENTHESISED = [
    "Manufacture of paper products for packaging (Unit II)",
    "Software consulting (SAP and Oracle implementation)",
    "Wholesale trade (import and export)",
    "Business support service activities (call centre and back office)",
    "Manufacture of components (gears, bearings, shafts)",
    "Provision of cloud infrastructure (IaaS/PaaS)",
]


@pytest.mark.parametrize("value", LEGIT_PARENTHESISED)
def test_5_legitimate_parenthesised_company_activities_are_accepted(value):
    assert is_valid_business_activity(value) is True


# --------------------------------------------------------------------------- #
# extractor: gloss line -> NOT_FOUND; clean / legit-paren line -> value
# --------------------------------------------------------------------------- #
def test_extractor_drops_gloss_line_keeps_clean_and_legit_parenthesised():
    gloss = (
        "Company Master Data\n"
        "Principal Business Activity: Other computer related activities "
        "[for example maintenance of websites of other clients]\n"
        "Company Status: Active\n"
    )
    assert extract_business_activity_from_text(gloss) == "NOT_FOUND"

    clean = "Nature of Business: Software development and IT consulting services\n"
    assert extract_business_activity_from_text(clean) == "Software development and IT consulting services"

    legit_paren = "Business Activity: Manufacture of paper products for packaging (Unit II)\n"
    assert extract_business_activity_from_text(legit_paren) == "Manufacture of paper products for packaging (Unit II)"


# --------------------------------------------------------------------------- #
# 6. a rejected activity value cannot become persisted / selected evidence
# --------------------------------------------------------------------------- #
@pytest.fixture(name="db")
def _db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _res(rid, fv, fn="business_activity", conf=0.8):
    return ResearchResult(
        result_id=rid, task_id="T", field_name=fn, field_value=fv, source_name="Third-Party Registry",
        source_url="https://registry.example/company/x", retrieved_at="2026-09-04T00:00:00Z",
        confidence=conf, verification_status="VERIFIED",
    )


def test_6_rejected_gloss_activity_is_not_persisted(db):
    payload = {"business_name": "SAMPLE ENTERPRISES PRIVATE LIMITED"}
    inv = Investigation(id=uuid.uuid4(), input_data=json.dumps(payload), raw_input=json.dumps(payload),
                        status="IN_PROGRESS")
    db.add(inv)
    db.commit()

    gloss = "Other computer related activities [for example maintenance of websites of other clients]"
    legit = "Software development and IT consulting services"

    # validation gate rejects the gloss, admits the legit value
    assert validate_research_result(_res("R1", gloss)).is_valid is False
    assert validate_research_result(_res("R2", legit)).is_valid is True

    saved = save_research_results(db, [_res("R1", gloss), _res("R2", legit)], inv.id)
    persisted = db.query(Evidence).filter(
        Evidence.investigation_id == inv.id, Evidence.field_name == "business_activity"
    ).all()
    values = {e.field_value for e in persisted}
    assert gloss not in values
    assert values == {legit}
