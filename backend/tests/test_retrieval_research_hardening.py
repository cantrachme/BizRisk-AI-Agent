import pytest
from unittest import mock
import re
from app.graph.state import ResearchTask, ResearchResult
from app.agents.browser import BrowserResearchAgent, detect_human_intervention


# 1. Exact GSTIN search query generation
def test_retrieval_01_exact_gstin_search_query():
    task = ResearchTask(
        task_id="T1",
        task_type="GST_VERIFICATION",
        target="27AAACT0627A1Z9",
        objective="GST verification",
        required_fields=["gst_status", "legal_name"],
        priority=1,
    )
    query = BrowserResearchAgent._build_search_query(task)
    assert query == '"27AAACT0627A1Z9"'


# 2. Exact CIN search query generation
def test_retrieval_02_exact_cin_search_query():
    task = ResearchTask(
        task_id="T2",
        task_type="MCA_VERIFICATION",
        target="L22210MH1995PLC084781",
        objective="MCA verification",
        required_fields=["company_status", "legal_name"],
        priority=1,
    )
    query = BrowserResearchAgent._build_search_query(task)
    assert query == '"L22210MH1995PLC084781"'


# 3. Exact identifier beats similar name in candidate scoring
def test_retrieval_03_exact_identifier_beats_similar_name():
    score1, reason1, rel1 = BrowserResearchAgent._score_candidate_url(
        "https://zaubacorp.com/company/APEX-DATA-SYSTEMS/27AAACT0627A1Z9",
        "APEX DATA SYSTEMS 27AAACT0627A1Z9",
        "THIRD_PARTY_RESEARCH"
    )
    score2, reason2, rel2 = BrowserResearchAgent._score_candidate_url(
        "https://zaubacorp.com/company/APEX-DATA-SYSTEMS-MUMBAI/33BBBCZ9999A1Z1",
        "APEX DATA SYSTEMS 27AAACT0627A1Z9",
        "THIRD_PARTY_RESEARCH"
    )
    assert score1 > score2
    assert rel1 == "TARGET_ENTITY"


# 4. Unrelated search result candidate rejected before navigation
def test_retrieval_04_unrelated_candidate_rejected_before_navigation():
    score, reason, rel = BrowserResearchAgent._score_candidate_url(
        "https://www.espncricinfo.com/series/ipl-2024",
        "Tata Consultancy Services Limited",
        "WEBSITE_VERIFICATION"
    )
    assert score < 0.40
    assert not BrowserResearchAgent._is_valid_candidate_url("https://www.espncricinfo.com/series/ipl-2024", "Tata Consultancy Services Limited", "WEBSITE_VERIFICATION")


# 5. Parent company candidate rejected as target direct evidence
def test_retrieval_05_parent_company_rejected_as_target_evidence():
    rel = BrowserResearchAgent._classify_entity_relationship(
        target="Tata Consultancy Services Limited",
        domain="tata.com",
        page_title="The Tata group. Leadership with Trust.",
        page_text="The Tata group comprises over 30 companies across ten verticals."
    )
    assert rel == "PARENT_ENTITY"
    
    task = ResearchTask(
        task_id="T5",
        task_type="WEBSITE_VERIFICATION",
        target="Tata Consultancy Services Limited",
        objective="Verify website",
        required_fields=["legal_name"],
        priority=1,
    )
    val, basis = BrowserResearchAgent._extract_field_value_with_basis(
        task=task,
        field_name="legal_name",
        page_data={"title": "The Tata group. Leadership with Trust.", "text": "Group overview", "url": "https://tata.com", "relationship": "PARENT_ENTITY"}
    )
    assert val == "NOT_FOUND"


# 6. Subsidiary rejected when target is parent
def test_retrieval_06_subsidiary_rejected_for_parent_target():
    score, reason, rel = BrowserResearchAgent._score_candidate_url(
        "https://www.tatamotors.com",
        "Tata Consultancy Services Limited",
        "WEBSITE_VERIFICATION"
    )
    # tata motors is a sister/subsidiary company, does not match consultancy services
    assert score < 0.40 or rel != "TARGET_ENTITY"


# 7. Wrong domain rejected
def test_retrieval_07_wrong_domain_rejected():
    score, reason, rel = BrowserResearchAgent._score_candidate_url(
        "https://www.hotelarchermumbai.com",
        "Tata Consultancy Services Limited",
        "WEBSITE_VERIFICATION"
    )
    assert score == 0.0
    assert rel == "UNRELATED"


# 8. Acronym / direct match domain accepted
def test_retrieval_08_acronym_domain_accepted():
    score, reason, rel = BrowserResearchAgent._score_candidate_url(
        "https://www.tcs.com",
        "Tata Consultancy Services Limited",
        "WEBSITE_VERIFICATION"
    )
    assert score >= 0.85
    assert rel == "TARGET_ENTITY"


# 9. Search-result page never becomes evidence
def test_retrieval_09_search_result_page_never_evidence():
    task = ResearchTask(
        task_id="T9",
        task_type="WEBSITE_VERIFICATION",
        target="Tata Consultancy Services Limited",
        objective="Website",
        required_fields=["legal_name"],
        priority=1,
    )
    val, basis = BrowserResearchAgent._extract_field_value_with_basis(
        task=task,
        field_name="legal_name",
        page_data={"title": "Tata Consultancy Services at DuckDuckGo", "text": "Search results for TCS", "url": "https://duckduckgo.com/?q=tcs"}
    )
    assert val == "NOT_FOUND"


# 10. Page loads but entity is irrelevant
def test_retrieval_10_irrelevant_loaded_page():
    html = "<html><title>Archer Hotel & Suites</title><body>Luxury rooms in Mumbai. Book now.</body></html>"
    flag = BrowserResearchAgent._is_failed_or_blocked_retrieval(html, "Tata Consultancy Services Limited")
    assert flag == "IRRELEVANT_SECTOR"


# 11. Correct official source selected
def test_retrieval_11_official_source_resolution():
    task = ResearchTask(
        task_id="T11",
        task_type="GST_VERIFICATION",
        target="27AAACT0627A1Z9",
        objective="GST",
        required_fields=["gst_status"],
        priority=1,
    )
    url = BrowserResearchAgent._resolve_url(task, "gst.gov.in", None)
    assert url == "https://services.gst.gov.in/services/searchtp"


# 12. CAPTCHA correctly detected
def test_retrieval_12_captcha_detection():
    html = "<html><title>GST Taxpayer Search</title><body>Please solve the captcha image: <img src='captcha.png'/></body></html>"
    inter = detect_human_intervention(html)
    assert inter == "CAPTCHA"


# 13. Blocked source preserved as unavailable
def test_retrieval_13_blocked_source_preservation():
    html = "<html><title>403 Forbidden - Access Denied</title><body>Your request was blocked by Cloudflare security.</body></html>"
    flag = BrowserResearchAgent._is_failed_or_blocked_retrieval(html, "TARGET CORP")
    assert flag == "BLOCKED_OR_ERROR"


# 14. No-result distinguished from blocked source
def test_retrieval_14_no_result_distinction():
    html = "<html><title>Taxpayer Search</title><body>No records found for the entered GSTIN.</body></html>"
    flag = BrowserResearchAgent._is_failed_or_blocked_retrieval(html, "TARGET CORP")
    assert flag == "NO_RESULTS"


# 15. Contextual extraction filters slogans and generic phrases
def test_retrieval_15_contextual_slogan_filtering():
    assert BrowserResearchAgent._clean_legal_name_candidate("The Tata group. Leadership with Trust.") is None
    assert BrowserResearchAgent._clean_legal_name_candidate("Where Quality Matters - Buy Online Lowest Prices") is None
    assert BrowserResearchAgent._clean_legal_name_candidate("Tata Consultancy Services Limited") == "Tata Consultancy Services Limited"
    assert BrowserResearchAgent._clean_legal_name_candidate("Tata Consultancy Services Limited - Official Site") == "Tata Consultancy Services Limited"
