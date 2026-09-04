"""
Regression for the three defects the real browser E2E exposed:

  1. third-party wrong-entity contamination (shared name token + conflicting id)
  2. general-web raw multi-MB page dump persisted as evidence
  3. a source organisation's own address mis-attributed to the target entity

All checks are generic - no company names, identifiers, or addresses are
special-cased.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.browser import BrowserResearchAgent
from app.db.base import Base
from app.graph.state import ResearchResult, ResearchTask
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.research.base import (
    extract_address_from_text,
    third_party_identity_verdict,
)
from app.risk.engine import calculate_risk_analysis
from app.services.evidence import save_research_results
from app.services.report import build_cross_source_consistency
from app.validation.research import validate_research_results


@pytest.fixture(name="db")
def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _mk_inv(db):
    inv = Investigation(id=uuid.uuid4(), input_data="{}", raw_input="{}", status="IN_PROGRESS")
    db.add(inv)
    db.commit()
    return inv.id


def _tp_task(target, fields=None):
    return ResearchTask(
        task_id="T1", task_type="THIRD_PARTY_RESEARCH", target=target, objective="verify",
        required_fields=fields or ["legal_name", "registered_address", "business_activity"],
        priority=1, preferred_sources=["zaubacorp.com"], fallback_sources=[],
    )


def _epfo_task(target):
    return ResearchTask(
        task_id="T1", task_type="EPFO_VERIFICATION", target=target, objective="verify",
        required_fields=["establishment_name", "epfo_status", "establishment_address"],
        priority=1, preferred_sources=["epfindia.gov.in"], fallback_sources=[],
    )


# --------------------------------------------------------------------------- #
# 1. shared name token + conflicting CIN  ->  zero evidence
# --------------------------------------------------------------------------- #
def test_1_shared_token_plus_conflicting_cin_yields_zero_evidence():
    target = "Alpha Consulting Services Limited U74140DL2001PLC000001"
    html = (
        "<html><head><title>Alpha Retail India Private Limited - Company Details | Directory</title></head>"
        "<body><h1>Alpha Retail India Private Limited</h1>"
        "<p>CIN: U52100MH2010PTC999999</p><p>Company Status: Active</p>"
        "<p>Registered Address: 1 Market Road, Pune, Maharashtra 411001</p>"
        "<p>Principal Business Activity: Retail sale of goods</p></body></html>"
    )
    assert third_party_identity_verdict(
        target, "Alpha Retail India Private Limited - Company Details | Directory",
        "Alpha Retail India Private Limited CIN U52100MH2010PTC999999",
    ) == "CONFLICT"

    results = BrowserResearchAgent(fetcher=lambda u: html).execute(_tp_task(target))
    assert validate_research_results(results).valid_results == []
    assert all(r.confidence == 0.0 for r in results)


def test_1b_shared_single_token_no_identifier_is_not_enough():
    # target has 3 distinctive tokens, page shares exactly one ("nova")
    assert third_party_identity_verdict(
        "Nova Structural Engineering Limited",
        "Nova Foods Private Limited - Profile | Registry",
        "Nova Foods Private Limited is engaged in food processing",
    ) in {"CONFLICT", "INSUFFICIENT"}
    results = BrowserResearchAgent(
        fetcher=lambda u: "<html><title>Nova Foods Private Limited - Profile</title>"
        "<body><h1>Nova Foods Private Limited</h1><p>Principal Business Activity: Food processing</p></body></html>"
    ).execute(_tp_task("Nova Structural Engineering Limited", fields=["legal_name", "business_activity"]))
    assert validate_research_results(results).valid_results == []


# --------------------------------------------------------------------------- #
# 2. unrelated third-party page  ->  zero evidence
# --------------------------------------------------------------------------- #
def test_2_unrelated_third_party_page_yields_zero_evidence():
    html = (
        "<html><head><title>Zenith Logistics Corporation - Registry</title></head>"
        "<body><h1>Zenith Logistics Corporation</h1>"
        "<p>Principal Business Activity: Freight transport</p>"
        "<p>Registered Address: 5 Dockyard Road, Chennai, Tamil Nadu 600001</p></body></html>"
    )
    results = BrowserResearchAgent(fetcher=lambda u: html).execute(
        _tp_task("Sample Enterprises Private Limited")
    )
    assert validate_research_results(results).valid_results == []


def test_2b_genuine_target_match_still_persists(db):
    html = (
        "<html><head><title>Sample Enterprises Private Limited - Company Profile | Registry</title></head>"
        "<body><h1>Sample Enterprises Private Limited</h1>"
        "<p>Company Status: Active</p>"
        "<p>Registered Address: 12 Industrial Estate, MIDC, Pune, Maharashtra 411018</p>"
        "<p>Principal Business Activity: Manufacture of processed foods</p></body></html>"
    )
    results = BrowserResearchAgent(fetcher=lambda u: html).execute(
        _tp_task("Sample Enterprises Private Limited", fields=["company_status", "registered_address", "business_activity"])
    )
    valid = validate_research_results(results).valid_results
    fields = {r.field_name for r in valid}
    assert "registered_address" in fields and "business_activity" in fields
    inv_id = _mk_inv(db)
    saved = save_research_results(db, valid, inv_id)
    assert any(e.field_name == "registered_address" for e in saved)


# --------------------------------------------------------------------------- #
# 3. multi-MB page dump  ->  not persisted   4. bounded useful text  ->  persists
# --------------------------------------------------------------------------- #
def _cand_result(source_text, rid="D1"):
    return ResearchResult(
        result_id=rid, task_id="T1", field_name="candidate_entities",
        field_value=[{"name": "TARGET WIDGETS LIMITED", "source_text": source_text, "confidence": 1.0}],
        source_name="General Web", source_url="https://example.com/x",
        retrieved_at="2026-09-02T12:00:00Z", confidence=0.6, verification_status=None,
    )


def test_3_multi_mb_page_dump_is_not_persisted(db):
    inv_id = _mk_inv(db)
    huge = "<html>" + ("<div>lorem ipsum dolor sit amet </div>" * 120_000) + "</html>"  # ~4.5 MB
    assert len(huge) > 4_000_000
    saved = save_research_results(db, [_cand_result(huge)], inv_id)
    assert len(saved) == 1
    row = db.query(Evidence).filter(Evidence.investigation_id == inv_id).one()
    assert len(row.field_value) < 30_000                      # bounded
    parsed = json.loads(row.field_value)
    assert parsed[0]["name"] == "TARGET WIDGETS LIMITED"      # discovery metadata kept
    assert len(str(parsed[0].get("source_text", ""))) <= 4000


def test_4_bounded_useful_discovery_text_persists(db):
    inv_id = _mk_inv(db)
    useful = (
        "<UNTRUSTED_WEBSITE_CONTENT>\nTarget Widgets Limited is a manufacturer of "
        "industrial widgets, incorporated 2009, registered in Pune.\n</UNTRUSTED_WEBSITE_CONTENT>"
    )
    saved = save_research_results(db, [_cand_result(useful, rid="D2")], inv_id)
    assert len(saved) == 1
    parsed = json.loads(db.query(Evidence).filter(Evidence.investigation_id == inv_id).one().field_value)
    assert "manufacturer of industrial widgets" in parsed[0]["source_text"]
    assert parsed[0]["name"] == "TARGET WIDGETS LIMITED"


def test_4b_browser_discovery_extraction_bounds_source_text():
    big_text = "Target Widgets Limited\n" + ("filler sentence about widgets. " * 5000)
    html = f"<html><head><title>Target Widgets Limited</title></head><body>{big_text}</body></html>"
    task = ResearchTask(
        task_id="T1", task_type="GENERAL_WEB_RESEARCH", target="Target Widgets Limited",
        objective="discover", required_fields=["candidate_entities"], priority=1,
        preferred_sources=["generic_web"], fallback_sources=[],
    )
    results = BrowserResearchAgent(fetcher=lambda u: html).execute(task)
    cand = next(r for r in results if r.field_name == "candidate_entities")
    for item in cand.field_value:
        assert len(str(item.get("source_text", ""))) <= 1400  # bounded at extraction


# --------------------------------------------------------------------------- #
# 5. source organisation's own address  ->  not target evidence
# 6. genuinely target-associated address  ->  persists
# --------------------------------------------------------------------------- #
_ORG_HOME_PAGE = (
    "Public Records Bureau\n"
    "Head Office\n"
    "Bureau House, 22 Central Avenue, New Capital City - 110001\n"
    "Helpline and Contact Us\n"
)

_TARGET_RECORD_PAGE = (
    "MERIDIAN TEXTILES LIMITED\n"
    "Establishment Code: XX/12345\n"
    "Establishment Address: Plot 7, Loom Industrial Area, Surat, Gujarat 395006\n"
    "Status: Active\n"
)


def test_5_source_org_own_address_not_attributed_to_target():
    assert extract_address_from_text(
        _ORG_HOME_PAGE, target="Meridian Textiles Limited EPFO establishment", target_confirmed=False
    ) == "NOT_FOUND"

    results = BrowserResearchAgent(
        fetcher=lambda u: f"<html><title>Public Records Bureau</title><body>{_ORG_HOME_PAGE}</body></html>"
    ).execute(_epfo_task("Meridian Textiles Limited EPFO establishment"))
    addr = next(r for r in results if r.field_name == "establishment_address")
    assert addr.field_value == "NOT_FOUND"
    assert addr.confidence == 0.0
    assert validate_research_results([r for r in results if r.field_name == "establishment_address"]).valid_results == []


def test_6_target_associated_epfo_address_persists(db):
    assert "Loom Industrial Area" in extract_address_from_text(
        _TARGET_RECORD_PAGE, target="Meridian Textiles Limited EPFO establishment", target_confirmed=False
    )
    results = BrowserResearchAgent(
        fetcher=lambda u: f"<html><title>MERIDIAN TEXTILES LIMITED</title><body>{_TARGET_RECORD_PAGE}</body></html>"
    ).execute(_epfo_task("Meridian Textiles Limited EPFO establishment"))
    addr = next(r for r in results if r.field_name == "establishment_address")
    assert "Loom Industrial Area" in str(addr.field_value)
    inv_id = _mk_inv(db)
    saved = save_research_results(db, [r for r in results if r.field_name == "establishment_address"], inv_id)
    assert any(e.field_name == "establishment_address" and "Surat" in e.field_value for e in saved)


def test_5b_bare_identifier_target_address_extraction_unchanged():
    # a bare CIN target has no name tokens -> association cannot apply -> prior behaviour
    page = "Registered Address: 74/2 Sarjapur Road, Bengaluru 560035, Karnataka\nStatus Active"
    assert "Sarjapur Road" in extract_address_from_text(
        page, target="L32102KA1945PLC020800", target_confirmed=False
    )


# --------------------------------------------------------------------------- #
# 7. rejected third-party contamination cannot create an address-mismatch risk
# --------------------------------------------------------------------------- #
def test_7_rejected_contamination_creates_no_address_mismatch_risk():
    def _res(fn, fv, rid, src, conf, vst):
        return ResearchResult(
            result_id=rid, task_id="T1", field_name=fn, field_value=fv, source_name=src,
            source_url="https://x/y", retrieved_at="2026-09-02T12:00:00Z",
            confidence=conf, verification_status=vst,
        )

    good = _res("registered_address", "9th Floor, Nirmal Building, Nariman Point, Mumbai 400021",
                "R1", "QuickCompany", 0.8, "VERIFIED")
    # wrong-company page rejected upstream by the identity gate
    contaminated = _res("registered_address", "No. 10, Jigani Industrial Area, Bangalore 560105",
                        "R2", "Tofler", 0.0, "REJECTED")

    valid = validate_research_results([good, contaminated]).valid_results
    assert [r.result_id for r in valid] == ["R1"]

    analysis = calculate_risk_analysis(valid)
    assert not any(s["code"] == "ADDRESS_MAJOR_MISMATCH" for s in analysis["risk_signals"])

    rec = build_cross_source_consistency(valid, {})
    addr_rec = next(r for r in rec if r["field_key"] == "registered_address")
    assert addr_rec["status"] in {"MATCH", "UNAVAILABLE"}
    assert all(s["source"] != "Tofler" for s in addr_rec["sources_compared"])
