import uuid
import pytest
from datetime import datetime, timezone

from app.graph.state import ResearchResult
from app.validation.research import validate_research_result, validate_research_results
from app.services.evidence import save_research_results
from app.services.report import build_cross_source_consistency
from app.research.base import (
    is_address_like,
    is_valid_legal_name,
    clean_legal_name_candidate,
    normalize_location,
    extract_address_from_text,
)
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.db.session import SessionLocal


def test_1_nature_of_business_activities_rejected_as_registered_address():
    """Test that 'Nature of Business Activities' and similar headings are rejected as addresses."""
    assert not is_address_like("Nature of Business Activities")
    assert not is_address_like("Nature of Business Activities:")
    assert not is_address_like("Nature of Business")
    assert not is_address_like("Principal Place of Business")
    assert not is_address_like("Registered Office Address")
    assert not is_address_like("Principal Business Activity")

    res = ResearchResult(
        result_id="RES-ADDR-001",
        task_id="TASK-001",
        field_name="registered_address",
        field_value="Nature of Business Activities",
        source_name="GST Portal",
        confidence=0.90,
        retrieved_at="2026-09-02T12:00:00Z",
    )
    val = validate_research_result(res)
    assert not val.is_valid
    assert any("not a valid address" in e for e in val.errors)


def test_2_business_activity_descriptions_rejected_as_addresses():
    """Test that activity descriptions (e.g. 'Software publishing, consultancy and supply') are rejected as addresses."""
    assert not is_address_like("Software publishing, consultancy and supply")
    assert not is_address_like("Manufacture of pharmaceuticals, medicinal chemical and botanical products")
    assert not is_address_like("Wholesale of electronic and telecommunications equipment and parts")

    res = ResearchResult(
        result_id="RES-ADDR-002",
        task_id="TASK-001",
        field_name="registered_address",
        field_value="Software publishing, consultancy and supply",
        source_name="GST Portal",
        confidence=0.85,
        retrieved_at="2026-09-02T12:00:00Z",
    )
    val = validate_research_result(res)
    assert not val.is_valid


def test_3_section_headings_and_ui_labels_rejected_as_addresses():
    """Test that portal labels, navigation items, and page headings are rejected as addresses."""
    assert not is_address_like("Search Taxpayer by GSTIN / UIN")
    assert not is_address_like("Company Master Data - Ministry of Corporate Affairs")
    assert not is_address_like("Directors / Signatory Details")
    assert not is_address_like("Terms of Service | Privacy Policy")


def test_4_search_snippets_rejected_as_legal_name_when_contaminated():
    """Test that search snippet noise is rejected as legal name."""
    assert not is_valid_legal_name("Search results for company registration in Maharashtra")
    assert not is_valid_legal_name("Welcome to official site - online shopping store")
    assert not is_valid_legal_name("404 Not Found - The requested URL was not found on this server")


def test_5_company_name_in_location_country_cleaned_or_rejected():
    """Test that 'Company Name in Location,Country' has contamination stripped or is rejected."""
    raw_candidate = "ALPHA LOGISTICS PRIVATE LIMITED in Maharashtra,India"
    cleaned = clean_legal_name_candidate(raw_candidate)
    assert cleaned == "ALPHA LOGISTICS PRIVATE LIMITED"
    assert "maharashtra" not in cleaned.lower()
    assert "india" not in cleaned.lower()

    # When validated directly, the uncleaned string with " in ..." is invalid
    assert not is_valid_legal_name("ALPHA LOGISTICS PRIVATE LIMITED in Maharashtra,India")

    # Cleaned name is valid
    assert is_valid_legal_name(cleaned)


def test_6_clean_company_name_is_accepted():
    """Test that legitimate corporate names are accepted."""
    assert is_valid_legal_name("ORION TECHNOLOGIES PRIVATE LIMITED")
    assert is_valid_legal_name("APEX ENGINEERING CORP")
    assert is_valid_legal_name("ZENITH ENTERPRISES LIMITED")


def test_7_location_metadata_does_not_contain_search_query_contamination():
    """Test that location metadata is normalized and cleaned of query artifacts."""
    loc1 = normalize_location("in maharastra,india")
    assert loc1 == "Maharastra, India"
    assert not loc1.lower().startswith("in ")

    loc2 = normalize_location("located in Bengaluru, Karnataka, India - official website")
    assert loc2 == "Bengaluru, Karnataka, India"
    assert "website" not in loc2.lower()

    loc3 = normalize_location("Delhi, New Delhi, India?q=search")
    assert loc3 == "Delhi, New Delhi, India"
    assert "?" not in loc3


def test_8_invalid_evidence_never_persisted_to_database():
    """Test that semantically invalid evidence rows are rejected by save_research_results."""
    inv_id = uuid.uuid4()
    with SessionLocal() as db:
        inv = Investigation(
            id=inv_id,
            input_data='{"business_name": "Test Company"}',
            status="IN_PROGRESS"
        )
        db.add(inv)
        db.commit()

        results = [
            ResearchResult(
                result_id="RES-INVALID-001",
                task_id="TASK-001",
                field_name="registered_address",
                field_value="Nature of Business Activities",
                source_name="GST Portal",
                confidence=0.90,
                retrieved_at="2026-09-02T12:00:00Z",
            ),
            ResearchResult(
                result_id="RES-INVALID-002",
                task_id="TASK-001",
                field_name="legal_name",
                field_value="ALPHA ENTERPRISES in Maharashtra, India",
                source_name="General Web",
                confidence=0.85,
                retrieved_at="2026-09-02T12:00:00Z",
            ),
            ResearchResult(
                result_id="RES-VALID-001",
                task_id="TASK-001",
                field_name="registered_address",
                field_value="Plot No 45, Sector 12, Industrial Area, Pune, Maharashtra 411018",
                source_name="MCA Portal",
                confidence=0.95,
                retrieved_at="2026-09-02T12:00:00Z",
            ),
        ]

        saved = save_research_results(db, results, inv_id)
        assert len(saved) == 1
        assert saved[0].research_result_id == "RES-VALID-001"

        # Check DB directly
        persisted = db.query(Evidence).filter(Evidence.investigation_id == inv_id).all()
        assert len(persisted) == 1
        assert persisted[0].field_name == "registered_address"
        assert "Plot No 45" in persisted[0].field_value

        db.query(Evidence).filter(Evidence.investigation_id == inv_id).delete()
        db.delete(inv)
        db.commit()


def test_9_invalid_evidence_never_reaches_reconciliation():
    """Test that invalid evidence is excluded from cross-source consistency reconciliation."""
    invalid_addr = ResearchResult(
        result_id="RES-BAD-001",
        task_id="TASK-001",
        field_name="registered_address",
        field_value="Nature of Business Activities",
        source_name="GST Portal",
        confidence=0.90,
        retrieved_at="2026-09-02T12:00:00Z",
    )
    valid_addr = ResearchResult(
        result_id="RES-GOOD-001",
        task_id="TASK-002",
        field_name="registered_address",
        field_value="9th Floor, Express Towers, Nariman Point, Mumbai 400021",
        source_name="MCA Portal",
        confidence=0.95,
        retrieved_at="2026-09-02T12:00:00Z",
    )

    # Validate results beforehand
    val_results = validate_research_results([invalid_addr, valid_addr])
    assert len(val_results.valid_results) == 1
    assert val_results.valid_results[0].result_id == "RES-GOOD-001"

    # Reconciliation on valid results
    rec = build_cross_source_consistency(val_results.valid_results, {})
    addr_rec = next(r for r in rec if r["field_key"] == "registered_address")
    assert addr_rec["status"] == "MATCH"
    assert len(addr_rec["sources_compared"]) == 1
    assert addr_rec["sources_compared"][0]["source"] == "MCA Portal"


def test_10_valid_address_and_legal_name_remain_accepted():
    """Test that clean, well-formed addresses and company names pass all validations."""
    addr = "Level 5, Building 3, Mindspace SEZ, Airoli, Navi Mumbai, Maharashtra - 400708"
    assert is_address_like(addr)

    res_addr = ResearchResult(
        result_id="RES-OK-1",
        task_id="T1",
        field_name="registered_address",
        field_value=addr,
        source_name="Tofler",
        confidence=0.90,
        retrieved_at="2026-09-02T12:00:00Z",
    )
    assert validate_research_result(res_addr).is_valid

    name = "BHARAT DYNAMICS LIMITED"
    assert is_valid_legal_name(name)
    res_name = ResearchResult(
        result_id="RES-OK-2",
        task_id="T1",
        field_name="legal_name",
        field_value=name,
        source_name="Zauba Corp",
        confidence=0.95,
        retrieved_at="2026-09-02T12:00:00Z",
    )
    assert validate_research_result(res_name).is_valid
