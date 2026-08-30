import pytest
import uuid
import re
import json
from datetime import datetime, timezone
from app.agents.browser import BrowserResearchAgent
from app.graph.state import ResearchTask, ResearchResult
from app.risk.rules import (
    normalize_evidence, 
    is_full_address, 
    evaluate_address_major_mismatch, 
    evaluate_very_recent_registration
)
from app.risk.engine import calculate_risk_analysis
from app.entity_resolution.resolver import resolve_entity

# 1. Generic homepage does not prove registered address.
def test_generic_homepage_no_registered_address():
    task = ResearchTask(
        task_id="T1", task_type="WEBSITE_RESEARCH", target="Wipro", objective="Find info",
        required_fields=["registered_address"], priority=1,
        preferred_sources=["company_website"], fallback_sources=[]
    )
    # Generic homepage mock HTML with no address info
    page_data = {
        "title": "Wipro Limited - Home Page",
        "text": "Wipro is a leading global information technology services and consulting company. Welcome to our website. Read our Privacy Policy. Contact us."
    }
    extracted = BrowserResearchAgent._extract_field_value(task, "registered_address", page_data)
    assert extracted == "NOT_FOUND"

# 2. Generic homepage does not prove incorporation date.
def test_generic_homepage_no_incorporation_date():
    task = ResearchTask(
        task_id="T1", task_type="WEBSITE_RESEARCH", target="Wipro", objective="Find info",
        required_fields=["incorporation_date"], priority=1,
        preferred_sources=["company_website"], fallback_sources=[]
    )
    page_data = {
        "title": "Wipro Limited - Home Page",
        "text": "Wipro is a leading global information technology services and consulting company. Welcome to our website. Read our Privacy Policy. Contact us."
    }
    extracted = BrowserResearchAgent._extract_field_value(task, "incorporation_date", page_data)
    assert extracted == "NOT_FOUND"

# 3. Browser success without relevant evidence remains UNVERIFIED (confidence 0.0).
def test_browser_success_no_evidence_unverified():
    res = ResearchResult(
        result_id="R1", task_id="T1", field_name="registered_address",
        field_value="NOT_FOUND", source_name="Company Website",
        source_url="https://wipro.com", retrieved_at="2026-08-30T00:00:00Z", confidence=0.85
    )
    norm = normalize_evidence(res)
    assert norm.confidence == 0.0

# 4. Browser error page is not evidence.
def test_browser_error_page_not_evidence():
    task = ResearchTask(
        task_id="T1", task_type="WEBSITE_RESEARCH", target="Wipro", objective="Find info",
        required_fields=["registered_address"], priority=1,
        preferred_sources=["company_website"], fallback_sources=[]
    )
    page_data = {
        "title": "Error 404",
        "text": "The page you are looking for was not found or has been moved."
    }
    extracted = BrowserResearchAgent._extract_field_value(task, "registered_address", page_data)
    assert extracted == "NOT_FOUND"

# 5. Third-party error page is not evidence.
def test_third_party_error_page_not_evidence():
    task = ResearchTask(
        task_id="T1", task_type="WEBSITE_RESEARCH", target="Wipro", objective="Find info",
        required_fields=["registered_address"], priority=1,
        preferred_sources=["third_party"], fallback_sources=[]
    )
    page_data = {
        "title": "DuckDuckGo Error",
        "text": "DuckDuckGo - Protection. Privacy. Peace of mind. Unexpected error. Please try again."
    }
    extracted = BrowserResearchAgent._extract_field_value(task, "registered_address", page_data)
    assert extracted == "NOT_FOUND"

# 6. City/state input must not automatically be treated as an exact registered address.
def test_city_state_input_no_mismatch():
    ref_addr = "Mumbai, Maharashtra, India"
    ext_addr = "Plot No. 12, Sector 4, Mumbai, Maharashtra 400001"
    
    assert not is_full_address(ref_addr)
    assert is_full_address(ext_addr)
    
    res1 = ResearchResult(
        result_id="R1", task_id="T1", field_name="registered_address",
        field_value=ref_addr, source_name="discovery_agent",
        source_url=None, retrieved_at="2026-08-30T00:00:00Z", confidence=1.0
    )
    res2 = ResearchResult(
        result_id="R2", task_id="T2", field_name="registered_address",
        field_value=ext_addr, source_name="GST Portal",
        source_url="https://gst.gov.in", retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    
    # Calculate risk analysis and verify no mismatch signal triggered
    analysis = calculate_risk_analysis([res1, res2])
    assert not any(sig["code"] == "ADDRESS_MAJOR_MISMATCH" for sig in analysis["risk_signals"])

# 7. Genuine registered-address conflict DOES trigger ADDRESS_MAJOR_MISMATCH.
def test_genuine_address_conflict_triggers_mismatch():
    addr1 = "Plot No. 12, Sector 4, Mumbai, Maharashtra 400001"
    addr2 = "Flat 3B, Residency Road, Bengaluru, Karnataka 560001"
    
    assert is_full_address(addr1)
    assert is_full_address(addr2)
    
    res1 = ResearchResult(
        result_id="R1", task_id="T1", field_name="registered_address",
        field_value=addr1, source_name="GST Portal",
        source_url="https://gst.gov.in", retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    res2 = ResearchResult(
        result_id="R2", task_id="T2", field_name="registered_address",
        field_value=addr2, source_name="MCA Registry",
        source_url="https://mca.gov.in", retrieved_at="2026-08-30T00:00:00Z", confidence=0.99
    )
    
    analysis = calculate_risk_analysis([res1, res2])
    assert any(sig["code"] == "ADDRESS_MAJOR_MISMATCH" for sig in analysis["risk_signals"])

# 8. Different contact/office address does not automatically create a registered-address mismatch.
def test_different_contact_vs_registered_office_no_mismatch():
    reg_addr = "Plot No. 12, Sector 4, Mumbai, Maharashtra 400001"
    contact_addr = "Flat 3B, Residency Road, Bengaluru, Karnataka 560001"
    
    res1 = ResearchResult(
        result_id="R1", task_id="T1", field_name="registered_address",
        field_value=reg_addr, source_name="GST Portal",
        source_url="https://gst.gov.in", retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    res2 = ResearchResult(
        result_id="R2", task_id="T2", field_name="contact_address",
        field_value=contact_addr, source_name="Company Website",
        source_url="https://wipro.com", retrieved_at="2026-08-30T00:00:00Z", confidence=0.85
    )
    
    analysis = calculate_risk_analysis([res1, res2])
    assert not any(sig["code"] == "ADDRESS_MAJOR_MISMATCH" for sig in analysis["risk_signals"])

# 9. Strong government/official evidence correctly verifies the field.
def test_official_evidence_verifies_field():
    page_data = {
        "title": "GST System Detail",
        "text": "Registered Address: Plot No 12, Sector 4, Mumbai, Maharashtra 400001"
    }
    extracted = BrowserResearchAgent._extract_address_from_text(page_data["text"])
    assert "Plot No 12" in extracted
    assert "Sector 4" in extracted

# 10. Entity match confidence can be high while individual fields remain unverified.
def test_entity_match_high_confidence_unverified_fields():
    target = {"business_name": "ABC Foods Private Limited", "gstin": "27ABCDE1234F1Z5"}
    candidates = [{"business_name": "ABC Foods Private Limited", "gstin": "27ABCDE1234F1Z5"}]
    
    # Entity Resolution succeeds with high confidence
    res = resolve_entity(target, candidates)
    assert res["matched"] is True
    assert res["confidence"] == 1.0
    
    # Individual address field remains unverified
    res_field = normalize_evidence(ResearchResult(
        result_id="R1", task_id="T1", field_name="registered_address",
        field_value="NOT_FOUND", source_name="Company Website",
        source_url="https://abcfoods.com", retrieved_at="2026-08-30T00:00:00Z", confidence=0.85
    ))
    assert res_field.confidence == 0.0

# 11. Missing evidence does not create a false risk signal.
def test_missing_evidence_no_risk_signal():
    res1 = ResearchResult(
        result_id="R1", task_id="T1", field_name="registered_address",
        field_value="NOT_FOUND", source_name="Company Website",
        source_url="https://wipro.com", retrieved_at="2026-08-30T00:00:00Z", confidence=0.85
    )
    # No address mismatch should trigger because R1 has confidence 0.0 and is excluded
    evs = [res1]
    analysis = calculate_risk_analysis(evs)
    # The investigation has insufficient evidence
    assert analysis["overall_risk"]["score"] is None
    assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"

# 12. Fallback source can correctly verify a previously unavailable field.
def test_fallback_verifies_unavailable_field():
    # Primary failed (confidence 0)
    res_fail = normalize_evidence(ResearchResult(
        result_id="R1", task_id="T1", field_name="registered_address",
        field_value="NOT_FOUND", source_name="GST Portal",
        source_url="https://gst.gov.in", retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    ))
    # Fallback succeeded
    res_success = normalize_evidence(ResearchResult(
        result_id="R2", task_id="T2", field_name="registered_address",
        field_value="Plot No. 12, Sector 4, Mumbai, Maharashtra 400001", source_name="Company Website",
        source_url="https://abcfoods.com", retrieved_at="2026-08-30T00:00:00Z", confidence=0.85
    ))
    
    assert res_fail.confidence == 0.0
    assert res_success.confidence == 0.85
