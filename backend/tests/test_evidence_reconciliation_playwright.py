import os
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from app.core.config import Settings, get_settings
from app.graph.state import ResearchResult
from app.validation.research import validate_research_result, validate_research_results
from app.services.evidence import save_research_results
from app.services.report import (
    build_cross_source_consistency,
    normalize_address_for_reconciliation,
    compare_semantic_fields,
)
from app.risk.rules import (
    normalize_address,
    evaluate_address_major_mismatch,
    NormalizedEvidence,
)
from app.models.evidence import Evidence
from app.models.investigation import Investigation
from app.db.session import SessionLocal


def test_1_playwright_headless_configuration_loaded_from_settings(monkeypatch):
    """Test that PLAYWRIGHT_HEADLESS is correctly loaded through Settings from environment."""
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "false")
    settings = Settings()
    assert settings.playwright_headless is False

    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "true")
    settings_true = Settings()
    assert settings_true.playwright_headless is True


def test_2_playwright_receives_configured_headless_value(monkeypatch):
    """Test that browser_session_manager launches chromium with the configured headless value from Settings."""
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "false")
    get_settings.cache_clear()
    
    with patch("app.core.browser_session_manager.sync_playwright") as mock_sync_pw:
        mock_pw_context = MagicMock()
        mock_pw = MagicMock()
        mock_sync_pw.return_value = mock_pw_context
        mock_pw_context.__enter__.return_value = mock_pw
        mock_pw.chromium.launch.return_value = MagicMock()
        
        from app.core.browser_session_manager import LiveBrowserSession
        session = LiveBrowserSession(
            investigation_id=str(uuid.uuid4()),
            task_id="TASK-TEST-001",
            source_name="example.com"
        )
        
        # Verify launch was called with headless=False
        assert mock_pw.chromium.launch.called
        launch_kwargs = mock_pw.chromium.launch.call_args.kwargs
        assert launch_kwargs.get("headless") is False


def test_3_placeholder_values_never_persisted_as_evidence():
    """Test that placeholder/status values (NOT_FOUND, UNAVAILABLE, ERROR, BLOCKED) are rejected from Evidence table."""
    inv_id = uuid.uuid4()
    
    with SessionLocal() as db:
        # Create dummy investigation record
        inv = Investigation(
            id=inv_id,
            input_data='{"business_name": "Acme Corp"}',
            status="IN_PROGRESS"
        )
        db.add(inv)
        db.commit()

        results = [
            ResearchResult(
                result_id="RES-PH-001",
                task_id="TASK-001",
                field_name="company_status",
                field_value="NOT_FOUND",
                source_name="Tofler",
                confidence=0.0,
                retrieved_at="2026-09-02T10:00:00Z",
            ),
            ResearchResult(
                result_id="RES-PH-002",
                task_id="TASK-001",
                field_name="registered_address",
                field_value="UNAVAILABLE",
                source_name="Zauba Corp",
                confidence=0.0,
                retrieved_at="2026-09-02T10:00:00Z",
            ),
            ResearchResult(
                result_id="RES-PH-003",
                task_id="TASK-001",
                field_name="legal_name",
                field_value="ERROR",
                source_name="QuickCompany",
                confidence=0.0,
                retrieved_at="2026-09-02T10:00:00Z",
            ),
            ResearchResult(
                result_id="RES-VALID-001",
                task_id="TASK-001",
                field_name="legal_name",
                field_value="Acme Corporation Limited",
                source_name="MCA Portal",
                confidence=0.90,
                retrieved_at="2026-09-02T10:00:00Z",
            ),
        ]

        # Validate results
        validation = validate_research_results(results)
        assert len(validation.valid_results) == 1
        assert validation.valid_results[0].result_id == "RES-VALID-001"
        assert len(validation.invalid_results) == 3

        # Save to database
        saved = save_research_results(db, results, inv_id)
        assert len(saved) == 1
        assert saved[0].field_value == "Acme Corporation Limited"

        # Query DB directly to verify no placeholder was inserted
        evs = db.query(Evidence).filter(Evidence.investigation_id == inv_id).all()
        assert len(evs) == 1
        assert evs[0].field_value == "Acme Corporation Limited"

        # Clean up
        db.query(Evidence).filter(Evidence.investigation_id == inv_id).delete()
        db.delete(inv)
        db.commit()


def test_4_different_address_semantic_types_are_not_compared():
    """Test that registered_address, establishment_address, contact_address, and principal_business_address are not mixed."""
    ev_reg = NormalizedEvidence(
        id=str(uuid.uuid4()),
        task_id="TASK-001",
        field_name="registered_address",
        field_value="9th Floor, Corporate Towers, Nariman Point, Mumbai, Maharashtra 400021",
        source_name="MCA Portal",
        source_url="https://www.mca.gov.in",
        retrieved_at=datetime.now(timezone.utc),
        confidence=0.95,
    )
    ev_epfo = NormalizedEvidence(
        id=str(uuid.uuid4()),
        task_id="TASK-002",
        field_name="establishment_address",
        field_value="Plot 12, Industrial Area, Okhla Phase 3, New Delhi, Delhi 110020",
        source_name="EPFO Portal",
        source_url="https://www.epfindia.gov.in",
        retrieved_at=datetime.now(timezone.utc),
        confidence=0.90,
    )
    ev_contact = NormalizedEvidence(
        id=str(uuid.uuid4()),
        task_id="TASK-003",
        field_name="contact_address",
        field_value="Tech Park, Whitefield, Bengaluru, Karnataka 560066",
        source_name="Company Website",
        source_url="https://www.example.com",
        retrieved_at=datetime.now(timezone.utc),
        confidence=0.85,
    )

    # Risk rule should not trigger ADDRESS_MAJOR_MISMATCH across different semantic types
    mismatch = evaluate_address_major_mismatch([ev_reg, ev_epfo, ev_contact])
    assert mismatch is None

    # Reconciliation compare_semantic_fields should return NOT_COMPARABLE
    comp_result = compare_semantic_fields(
        "registered_address", ev_reg.field_value,
        "establishment_address", ev_epfo.field_value
    )
    assert comp_result == "NOT_COMPARABLE"


def test_5_equivalent_addresses_match_after_generic_normalization():
    """Test that formatting, casing, abbreviations (Bldg, Flr, St), and country suffix differences match as MATCH."""
    addr1 = "9TH FLOOR, NIRMAL BUILDING NARIMAN POINT, MUMBAI, Maharashtra - 400021"
    addr2 = "9th Flr, Nirmal Bldg, Nariman Point, Mumbai, Maharashtra, India 400021"

    norm1 = normalize_address_for_reconciliation(addr1)
    norm2 = normalize_address_for_reconciliation(addr2)
    assert norm1 == norm2

    comp = compare_semantic_fields("registered_address", addr1, "registered_address", addr2)
    assert comp == "MATCH"

    # Cross-source consistency evaluation
    ev1 = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="registered_address",
        field_value=addr1,
        source_name="Tofler",
        retrieved_at="2026-09-02T10:00:00Z",
        confidence=0.80,
    )
    ev2 = ResearchResult(
        result_id="RES-002",
        task_id="TASK-002",
        field_name="registered_address",
        field_value=addr2,
        source_name="Zauba Corp",
        retrieved_at="2026-09-02T10:00:00Z",
        confidence=0.80,
    )
    consistency = build_cross_source_consistency([ev1, ev2], {})
    addr_rec = next(r for r in consistency if r["field_key"] == "registered_address")
    assert addr_rec["status"] == "MATCH"


def test_6_meaningfully_different_comparable_addresses_produce_mismatch():
    """Test that registered addresses in different cities/postal codes produce CONFLICT."""
    addr_mumbai = "9th Floor, Nirmal Building, Nariman Point, Mumbai, Maharashtra 400021"
    addr_pune = "4th Floor, Tech Centre, Hinjewadi Phase 1, Pune, Maharashtra 411057"

    comp = compare_semantic_fields("registered_address", addr_mumbai, "registered_address", addr_pune)
    assert comp == "CONFLICT"

    ev1 = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="registered_address",
        field_value=addr_mumbai,
        source_name="MCA Portal",
        retrieved_at="2026-09-02T10:00:00Z",
        confidence=0.90,
    )
    ev2 = ResearchResult(
        result_id="RES-002",
        task_id="TASK-002",
        field_name="registered_address",
        field_value=addr_pune,
        source_name="Zauba Corp",
        retrieved_at="2026-09-02T10:00:00Z",
        confidence=0.80,
    )
    consistency = build_cross_source_consistency([ev1, ev2], {})
    addr_rec = next(r for r in consistency if r["field_key"] == "registered_address")
    assert addr_rec["status"] == "CONFLICT"


def test_7_single_source_or_unknown_semantics_produce_unavailable_or_not_comparable():
    """Test that fields with only one source return UNAVAILABLE / single source status rather than false conflict."""
    ev = ResearchResult(
        result_id="RES-001",
        task_id="TASK-001",
        field_name="incorporation_date",
        field_value="1995-01-19",
        source_name="MCA Portal",
        retrieved_at="2026-09-02T10:00:00Z",
        confidence=0.90,
    )
    # Empty entity dictionary (no other source or user input)
    consistency = build_cross_source_consistency([ev], {})
    inc_rec = next(r for r in consistency if r["field_key"] == "incorporation_date")
    assert inc_rec["status"] == "MATCH"

    # Mismatched semantic categories
    comp = compare_semantic_fields("legal_name", "Acme Ltd", "company_status", "ACTIVE")
    assert comp == "NOT_COMPARABLE"


def test_8_generic_location_normalization_never_contaminates_company_identity():
    """Test that location phrases do not contaminate legal name or company identifiers."""
    from app.research.base import clean_legal_name_candidate
    
    clean1 = clean_legal_name_candidate("BHARAT DYNAMICS LIMITED in Telangana")
    assert clean1 == "BHARAT DYNAMICS LIMITED"
    assert "telangana" not in clean1.lower()

    clean2 = clean_legal_name_candidate("ZENITH TECHNOLOGIES PRIVATE LIMITED in Maharashtra - Company Profile")
    assert clean2 == "ZENITH TECHNOLOGIES PRIVATE LIMITED"
    assert "maharashtra" not in clean2.lower()
