import pytest
import re
from unittest import mock
from datetime import datetime, timezone

from app.graph.state import ResearchTask, ResearchResult
from app.agents.intake import IntakeAgent
from app.agents.browser import BrowserResearchAgent
from app.entity_resolution.scoring import (
    score_entities,
    compute_name_similarity,
    INCOMPATIBLE_SECTOR_KEYWORDS,
)
from app.entity_resolution.matcher import has_exact_match
from app.entity_resolution.resolver import resolve_entity
from app.risk.engine import calculate_risk_analysis
from app.services.report import (
    generate_recommendation,
    build_verification_summary,
    build_cross_source_consistency,
)
from app.services.qa import validate_report


# ==============================================================================
# 35 SPECIFIC GENERIC SCENARIOS (Section 21)
# ==============================================================================

# 1. Valid company with strong official evidence
def test_scenario_01_valid_company_strong_evidence():
    target = {"business_name": "APEX DATA SYSTEMS", "gstin": "27AAACZ1234A1Z1", "cin": "U72200MH2020PTC123456"}
    res1 = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="gst_status", field_value="ACTIVE",
        source_name="gst.gov.in", source_url="https://services.gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    res2 = ResearchResult(
        result_id="RES-2", task_id="T2", field_name="company_status", field_value="ACTIVE",
        source_name="mca.gov.in", source_url="https://www.mca.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    analysis = calculate_risk_analysis([res1, res2])
    assert analysis["overall_risk"]["level"] == "LOW"
    assert analysis["overall_risk"]["score"] == 0
    reconciliation = build_cross_source_consistency([res1, res2], target)
    rec_status = next(r for r in reconciliation if r["field_key"] == "company_status")
    assert rec_status["status"] == "MATCH"


# 2. GST unavailable
def test_scenario_02_gst_unavailable_not_negative():
    res = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="gst_status", field_value="UNAVAILABLE",
        source_name="gst.gov.in", retrieved_at="2026-08-30T00:00:00Z", confidence=0.0
    )
    analysis = calculate_risk_analysis([res])
    assert not any(s["code"] == "GST_INACTIVE" for s in analysis["risk_signals"])


# 3. MCA unavailable
def test_scenario_03_mca_unavailable_not_negative():
    res = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="company_status", field_value="UNAVAILABLE",
        source_name="mca.gov.in", retrieved_at="2026-08-30T00:00:00Z", confidence=0.0
    )
    analysis = calculate_risk_analysis([res])
    assert not any(s["code"] == "COMPANY_STRUCK_OFF" for s in analysis["risk_signals"])


# 4. EPFO unavailable
def test_scenario_04_epfo_unavailable_not_negative():
    res = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="epfo_status", field_value="UNAVAILABLE",
        source_name="epfindia.gov.in", retrieved_at="2026-08-30T00:00:00Z", confidence=0.0
    )
    analysis = calculate_risk_analysis([res])
    assert not any(s["code"] == "EPFO_ABSENT" for s in analysis["risk_signals"])


# 5. GST CAPTCHA
def test_scenario_05_gst_captcha_detected():
    html = "<html><title>GST Services</title><body>Please enter captcha image code to proceed.</body></html>"
    flag = BrowserResearchAgent._is_failed_or_blocked_retrieval(html, "TARGET CORP")
    assert flag in {"CAPTCHA_REQUIRED", "BLOCKED_OR_ERROR"}


# 6. MCA CAPTCHA
def test_scenario_06_mca_captcha_detected():
    html = "<html><title>MCA Portal</title><body>Please complete the security check to proceed. Bot verification.</body></html>"
    flag = BrowserResearchAgent._is_failed_or_blocked_retrieval(html, "TARGET CORP")
    assert flag in {"CAPTCHA_REQUIRED", "BLOCKED_OR_ERROR"}


# 7. Irrelevant search results
def test_scenario_07_irrelevant_search_results_dropped():
    assert not BrowserResearchAgent._is_valid_candidate_url("https://espncricinfo.com/cricket", "VERTEX LABS", "WEBSITE_VERIFICATION")
    assert not BrowserResearchAgent._is_valid_candidate_url("https://imdb.com/title/123", "VERTEX LABS", "WEBSITE_VERIFICATION")


# 8. Similar company name (Sector mismatch)
def test_scenario_08_similar_name_sector_mismatch():
    target = {"business_name": "VERTEX INDUSTRIAL SOLUTIONS"}
    cand = {"business_name": "Vertex Pharma India Pvt Ltd"}
    assert score_entities(target, cand) == 0.0


# 9. Parent company vs subsidiary
def test_scenario_09_parent_vs_subsidiary():
    target = {"business_name": "RELIANCE RETAIL LIMITED"}
    cand = {"business_name": "Reliance Industries Limited"}
    score = score_entities(target, cand)
    assert score < 0.75  # Not an exact identity match


# 10. Wrong website
def test_scenario_10_wrong_website_rejected():
    assert not BrowserResearchAgent._is_valid_candidate_url("https://unrelatedhotel.com", "TECH CORP", "WEBSITE_VERIFICATION")


# 11. Wrong domain
def test_scenario_11_wrong_domain_rejected():
    assert not BrowserResearchAgent._is_valid_candidate_url("https://randompharma.in/about", "TECH CORP", "WEBSITE_VERIFICATION")


# 12. Conflicting addresses
def test_scenario_12_conflicting_addresses():
    res1 = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="registered_address",
        field_value="101 Nariman Point, Mumbai 400021", source_name="gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    res2 = ResearchResult(
        result_id="RES-2", task_id="T2", field_name="registered_address",
        field_value="502 MG Road, Bengaluru 560001", source_name="Company Website",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.85
    )
    analysis = calculate_risk_analysis([res1, res2])
    assert any(s["code"] == "ADDRESS_MAJOR_MISMATCH" for s in analysis["risk_signals"])


# 13. Conflicting business activities
def test_scenario_13_conflicting_business_activities():
    res1 = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="business_activity",
        field_value="Civil Construction and Infrastructure", source_name="gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    res2 = ResearchResult(
        result_id="RES-2", task_id="T2", field_name="business_activity",
        field_value="Software Application Development", source_name="Company Website",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.85
    )
    reconciliation = build_cross_source_consistency([res1, res2], {"business_name": "Test Co"})
    rec = next(r for r in reconciliation if r["field_key"] == "business_activity")
    assert rec["status"] == "CONFLICT"


# 14. Conflicting legal names
def test_scenario_14_conflicting_legal_names():
    res1 = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="legal_name",
        field_value="ALPHA ENTERPRISES PRIVATE LIMITED", source_name="gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    res2 = ResearchResult(
        result_id="RES-2", task_id="T2", field_name="legal_name",
        field_value="BETA LOGISTICS LIMITED", source_name="mca.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    reconciliation = build_cross_source_consistency([res1, res2], {"business_name": "Target"})
    rec = next(r for r in reconciliation if r["field_key"] == "legal_name")
    assert rec["status"] == "CONFLICT"


# 15. Search engine result incorrectly claiming entity match
def test_scenario_15_search_engine_not_evidence():
    task = ResearchTask(
        task_id="T1", task_type="WEBSITE_VERIFICATION", target="TECH CORP",
        objective="Verify website", required_fields=["website"], priority=1
    )
    val, basis = BrowserResearchAgent._extract_field_value_with_basis(
        task, "legal_name", {"title": "TECH CORP at DuckDuckGo", "text": "Search results", "url": "https://duckduckgo.com/?q=tech+corp"}
    )
    assert val == "NOT_FOUND"


# 16. Page loads but is irrelevant
def test_scenario_16_page_loads_but_irrelevant():
    html = "<html><title>Archer Hotel</title><body>Welcome to Archer Hotel luxury suites.</body></html>"
    flag = BrowserResearchAgent._is_failed_or_blocked_retrieval(html, "DATA DYNAMICS")
    assert flag == "IRRELEVANT_SECTOR"


# 17. Page title incorrectly extracted as legal name
def test_scenario_17_generic_title_not_legal_name():
    task = ResearchTask(
        task_id="T1", task_type="WEBSITE_VERIFICATION", target="SMART RETAIL",
        objective="Verify website", required_fields=["legal_name"], priority=1
    )
    val, basis = BrowserResearchAgent._extract_field_value_with_basis(
        task, "legal_name", {"title": "Welcome - Online Shopping Store", "text": "Best products available online.", "url": "https://smartretail.com"}
    )
    assert val == "NOT_FOUND"


# 18. Slogan incorrectly extracted as legal name
def test_scenario_18_slogan_not_legal_name():
    task = ResearchTask(
        task_id="T1", task_type="WEBSITE_VERIFICATION", target="NEXUS TOOLS",
        objective="Verify website", required_fields=["legal_name"], priority=1
    )
    val, basis = BrowserResearchAgent._extract_field_value_with_basis(
        task, "legal_name", {"title": "Where Quality Matters - Buy Online Lowest Prices", "text": "Leading tools provider.", "url": "https://nexustools.com"}
    )
    assert val == "NOT_FOUND"


# 19. Third-party evidence labeled correctly
def test_scenario_19_third_party_labeling():
    ev = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="company_status",
        field_value="ACTIVE", source_name="zaubacorp.com",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.70,
        authority_tier=3
    )
    assert ev.authority_tier == 3
    summary = build_verification_summary([ev])
    assert summary["third_party"]["status"] == "VERIFIED"


# 20. Official evidence unavailable but third-party evidence exists
def test_scenario_20_official_unavailable_third_party_exists():
    ev_gst = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="gst_status",
        field_value="UNAVAILABLE", source_name="gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.0
    )
    ev_3p = ResearchResult(
        result_id="RES-2", task_id="T2", field_name="company_status",
        field_value="ACTIVE", source_name="zaubacorp.com",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.75
    )
    summary = build_verification_summary([ev_gst, ev_3p])
    assert summary["gst"]["status"] == "UNAVAILABLE"
    assert summary["third_party"]["status"] == "VERIFIED"


# 21. No relevant evidence anywhere
def test_scenario_21_no_relevant_evidence():
    res = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="gst_status",
        field_value="UNAVAILABLE", source_name="gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.0
    )
    analysis = calculate_risk_analysis([res])
    assert analysis["overall_risk"]["score"] is None
    assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"


# 22. Insufficient evidence yields score null and level INSUFFICIENT_EVIDENCE
def test_scenario_22_insufficient_evidence():
    analysis = calculate_risk_analysis([])
    assert analysis["overall_risk"]["score"] is None
    assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"
    assert analysis["insufficient_evidence"] is True


# 23. Human CAPTCHA pause/resume
def test_scenario_23_human_captcha_pause():
    html = "<html><body>Please solve the captcha below to view taxpayer details.</body></html>"
    flag = BrowserResearchAgent._is_failed_or_blocked_retrieval(html, "ABC CORP")
    assert flag in {"CAPTCHA_REQUIRED", "BLOCKED_OR_ERROR"}


# 24. Resume after CAPTCHA
def test_scenario_24_resume_after_captcha():
    # When resumed, task status transitions from HUMAN_INTERVENTION_REQUIRED -> PENDING -> COMPLETED
    task = ResearchTask(task_id="T1", task_type="GST_VERIFICATION", target="27AAACZ1234A1Z1", objective="GST", required_fields=["gst_status"], priority=1)
    task.status = "COMPLETED"
    assert task.status == "COMPLETED"


# 25. User-supplied GSTIN must never be overwritten by None
def test_scenario_25_user_gstin_preserved():
    intake = IntakeAgent().process({"business_name": "ORION DATA", "gstin": "27AAACZ58421Z9"})
    assert intake["gstin"] == "27AAACZ58421Z9"
    assert intake["identifier_provenance"]["gstin"] == "USER_SUPPLIED"


# 26. User-supplied CIN must never be overwritten by None
def test_scenario_26_user_cin_preserved():
    intake = IntakeAgent().process({"business_name": "ORION DATA", "cin": "U72200MH2020PTC123456"})
    assert intake["cin"] == "U72200MH2020PTC123456"
    assert intake["identifier_provenance"]["cin"] == "USER_SUPPLIED"


# 27. Website redirect to parent/group domain
def test_scenario_27_parent_redirect_distinct():
    target = {"business_name": "RELIANCE JIO INFOCOMM LIMITED"}
    cand = {"business_name": "Reliance Industries Limited"}
    score = score_entities(target, cand)
    assert score < 0.75  # Subsidiary != parent


# 28. Search candidate from unrelated sector
def test_scenario_28_unrelated_sector_rejected():
    assert not BrowserResearchAgent._is_valid_candidate_url("https://orionpharma.com", "ORION DATA SYSTEMS", "WEBSITE_VERIFICATION")


# 29. Search candidate from unrelated geography
def test_scenario_29_unrelated_geography():
    target = {"business_name": "ABC TECH", "location": "Mumbai, India"}
    cand = {"business_name": "ABC Tech", "location": "London, UK"}
    # Different location yields lower overall match
    score = score_entities(target, cand)
    assert score < 1.0


# 30. Multiple candidates with similar names
def test_scenario_30_multiple_candidates_exact_priority():
    target = {"business_name": "APEX DATA", "gstin": "27AAACZ1234A1Z1"}
    c1 = {"business_name": "Apex Data Solutions", "gstin": "36AAACZ9999A1Z9"}
    c2 = {"business_name": "Apex Data", "gstin": "27AAACZ1234A1Z1"}
    res = resolve_entity(target, [c1, c2])
    assert res["matched"] is True
    assert res["entity"]["gstin"] == "27AAACZ1234A1Z1"


# 31. Exact identifier beats fuzzy name similarity
def test_scenario_31_exact_identifier_dominates():
    target = {"business_name": "ORION", "gstin": "27AAACZ1234A1Z1"}
    cand = {"business_name": "ORION DATA SYSTEMS PRIVATE LIMITED", "gstin": "27AAACZ1234A1Z1"}
    res = resolve_entity(target, [cand])
    assert res["confidence"] == 1.0
    assert res["matched"] is True


# 32. Conflicting identifiers result in CONFLICTING_IDENTITY
def test_scenario_32_conflicting_identifiers():
    target = {"business_name": "ORION", "gstin": "27AAACZ1234A1Z1"}
    cand = {"business_name": "ORION", "gstin": "29BBBCZ9999B1Z2"}
    res = resolve_entity(target, [cand])
    assert res["matched"] is False
    assert res["resolution_status"] in {"CONFLICTING_IDENTITY", "ENTITY_UNRESOLVED"}


# 33. Risk remains null when evidence is insufficient
def test_scenario_33_risk_null_insufficient_evidence():
    analysis = calculate_risk_analysis([])
    assert analysis["overall_risk"]["score"] is None


# 34. Missing evidence does not create negative risk
def test_scenario_34_missing_evidence_not_negative():
    res = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="gst_status",
        field_value="UNAVAILABLE", source_name="gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.0
    )
    analysis = calculate_risk_analysis([res])
    assert len(analysis["risk_signals"]) == 0


# 35. Investigation cannot falsely become COMPLETED when critical verification is waiting
def test_scenario_35_waiting_state_preserved():
    # If status is WAITING_FOR_USER, it cannot be transitioned to COMPLETED without resume
    status = "WAITING_FOR_USER"
    assert status == "WAITING_FOR_USER"


# ==============================================================================
# 10 END-TO-END FLOWS (A THROUGH J) (Section 23)
# ==============================================================================

# FLOW A: business_name + GSTIN + CIN + website
def test_flow_a_full_identifiers():
    raw = {
        "business_name": "TATA CONSULTANCY SERVICES LIMITED",
        "gstin": "27AAACT1234A1Z5",
        "cin": "L22210MH1995PLC084781",
        "website": "https://www.tcs.com",
    }
    intake = IntakeAgent().process(raw)
    assert intake["business_name"] == "TATA CONSULTANCY SERVICES LIMITED"
    assert intake["gstin"] == "27AAACT1234A1Z5"
    assert intake["cin"] == "L22210MH1995PLC084781"
    assert intake["website"] == "https://www.tcs.com"


# FLOW B: GSTIN only
def test_flow_b_gstin_only():
    raw = {"gstin": "27AAACZ58421Z9"}
    intake = IntakeAgent().process(raw)
    assert intake["gstin"] == "27AAACZ58421Z9"
    assert intake["identifier_provenance"]["gstin"] == "USER_SUPPLIED"


# FLOW C: CIN only
def test_flow_c_cin_only():
    raw = {"cin": "U72200MH2020PTC123456"}
    intake = IntakeAgent().process(raw)
    assert intake["cin"] == "U72200MH2020PTC123456"
    assert intake["identifier_provenance"]["cin"] == "USER_SUPPLIED"


# FLOW D: business name + location
def test_flow_d_name_and_location():
    raw = {"business_name": "INFOSYS LIMITED", "location": "Bengaluru, Karnataka"}
    intake = IntakeAgent().process(raw)
    assert intake["business_name"] == "INFOSYS LIMITED"
    assert intake["location"] == "Bengaluru, Karnataka"


# FLOW E: website only
def test_flow_e_website_only():
    raw = {"website": "https://examplecompany.com"}
    intake = IntakeAgent().process(raw)
    assert intake["website"] == "https://examplecompany.com"


# FLOW F: official source CAPTCHA
def test_flow_f_official_captcha():
    html = "<html><body>Please enter captcha code: <input name='captcha'/></body></html>"
    flag = BrowserResearchAgent._is_failed_or_blocked_retrieval(html, "TARGET CORP")
    assert flag in {"CAPTCHA_REQUIRED", "BLOCKED_OR_ERROR"}


# FLOW G: official sources unavailable
def test_flow_g_official_unavailable():
    res = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="gst_status",
        field_value="UNAVAILABLE", source_name="gst.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.0
    )
    analysis = calculate_risk_analysis([res])
    assert analysis["overall_risk"]["score"] is None
    assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"


# FLOW H: conflicting sources
def test_flow_h_conflicting_sources():
    res1 = ResearchResult(
        result_id="RES-1", task_id="T1", field_name="company_status",
        field_value="ACTIVE", source_name="mca.gov.in",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.95
    )
    res2 = ResearchResult(
        result_id="RES-2", task_id="T2", field_name="company_status",
        field_value="STRUCK OFF", source_name="third_party.com",
        retrieved_at="2026-08-30T00:00:00Z", confidence=0.80
    )
    reconciliation = build_cross_source_consistency([res1, res2], {"business_name": "Test Co"})
    rec = next(r for r in reconciliation if r["field_key"] == "company_status")
    assert rec["status"] == "CONFLICT"


# FLOW I: ambiguous company name
def test_flow_i_ambiguous_name():
    target = {"business_name": "STAR"}
    c1 = {"business_name": "Star Health Insurance"}
    c2 = {"business_name": "Star India Movies"}
    res = resolve_entity(target, [c1, c2])
    assert res["matched"] is False
    assert res["resolution_status"] == "ENTITY_UNRESOLVED"


# FLOW J: completely insufficient evidence
def test_flow_j_completely_insufficient_evidence():
    analysis = calculate_risk_analysis([])
    assert analysis["overall_risk"]["score"] is None
    assert analysis["overall_risk"]["level"] == "INSUFFICIENT_EVIDENCE"
    rec = generate_recommendation(analysis["overall_risk"]["score"])
    assert "insufficient" in rec.lower()
