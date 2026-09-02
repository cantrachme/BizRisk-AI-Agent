import pytest
from unittest import mock
from fastapi.testclient import TestClient
from app.main import app as fastapi_app

client = TestClient(fastapi_app)

@pytest.fixture(name="client_override")
def fixture_client_override():
    return client

def test_gstin_cannot_become_legal_name_or_address_or_status(client_override):
    # Tests 1, 2, 3: GSTIN cannot become legal_name, gst_status, or registered_address
    mock_html = "<html><body>GSTIN 27AAACW0387R1Z6 search results. Access denied.</body></html>"
    payload = {
        "task_id": "TASK-REG-01",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name", "gst_status", "registered_address"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
    
    assert resp.status_code == 200
    data = resp.json()
    fields = {r["field_name"]: r for r in data["results"]}
    
    # 1. GSTIN cannot become legal_name
    assert fields["legal_name"]["field_value"] == "NOT_FOUND"
    assert fields["legal_name"]["confidence"] == 0.0
    
    # 2. GSTIN cannot become gst_status
    assert fields["gst_status"]["field_value"] == "UNAVAILABLE"
    assert fields["gst_status"]["confidence"] == 0.0
    
    # 3. GSTIN cannot become registered_address
    assert fields["registered_address"]["field_value"] == "NOT_FOUND"
    assert fields["registered_address"]["confidence"] == 0.0

def test_cin_cannot_become_legal_name_status_date_or_address(client_override):
    # Tests 4, 5, 6, 7: CIN cannot become legal_name, company_status, incorporation_date, or registered_address
    mock_html = "<html><body>CIN L32102KA1945PLC020800 MCA records. Connection timed out.</body></html>"
    payload = {
        "task_id": "TASK-REG-02",
        "task_type": "MCA_VERIFICATION",
        "target": "L32102KA1945PLC020800",
        "objective": "Verify MCA status",
        "required_fields": ["legal_name", "company_status", "incorporation_date", "registered_address"],
        "priority": 1,
        "preferred_sources": ["mca.gov.in"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    fields = {r["field_name"]: r for r in data["results"]}
    
    # 4. CIN cannot become legal_name
    assert fields["legal_name"]["field_value"] == "NOT_FOUND"
    # 5. CIN cannot become company_status
    assert fields["company_status"]["field_value"] == "NOT_FOUND"
    # 6. CIN cannot become incorporation_date
    assert fields["incorporation_date"]["field_value"] == "NOT_FOUND"
    # 7. CIN cannot become registered_address
    assert fields["registered_address"]["field_value"] == "NOT_FOUND"

def test_website_url_cannot_become_address_or_year(client_override):
    # Tests 8, 9: Website URL cannot become contact_address or established_year
    mock_html = "<html><body>Welcome to https://www.wipro.com homepage! Contact us.</body></html>"
    payload = {
        "task_id": "TASK-REG-03",
        "task_type": "WEBSITE_VERIFICATION",
        "target": "https://www.wipro.com",
        "objective": "Verify website claims",
        "required_fields": ["contact_address", "established_year"],
        "priority": 2,
        "preferred_sources": ["company_website"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    fields = {r["field_name"]: r for r in data["results"]}
    
    # 8. Website URL cannot become contact_address
    assert fields["contact_address"]["field_value"] == "NOT_FOUND"
    # 9. Website URL cannot become established_year
    assert fields["established_year"]["field_value"] == "NOT_FOUND"

def test_generic_homepage_without_requested_evidence_returns_not_found(client_override):
    # Test 10: Generic homepage without requested evidence returns NOT_FOUND
    mock_html = "<html><body>Welcome to our generic business landing page! We offer IT consulting.</body></html>"
    payload = {
        "task_id": "TASK-REG-04",
        "task_type": "WEBSITE_VERIFICATION",
        "target": "https://example.com",
        "objective": "Verify website claims",
        "required_fields": ["established_year"],
        "priority": 2,
        "preferred_sources": ["company_website"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0

def test_error_blocked_page_does_not_become_evidence(client_override):
    # Test 11: Error/blocked page does not become evidence
    mock_html = "<html><body>503 Service Temporarily Unavailable. Cloudflare Protection.</body></html>"
    payload = {
        "task_id": "TASK-REG-05",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["registered_address"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0

def test_genuine_structured_evidence_extracted_correctly(client_override):
    # Test 12: Genuine structured evidence is still extracted correctly
    mock_html = """
    <html>
    <head><title>WIPRO LIMITED</title></head>
    <body>
      <div>Incorporation date: 29/12/1945</div>
      <div>Company Status: struck off</div>
      <div>Registered Address: 74/2, Doddakannelli, Sarjapur Road, Bengaluru 560035, Karnataka</div>
    </body>
    </html>
    """
    payload = {
        "task_id": "TASK-REG-06",
        "task_type": "MCA_VERIFICATION",
        "target": "L32102KA1945PLC020800",
        "objective": "Verify MCA details",
        "required_fields": ["legal_name", "company_status", "incorporation_date", "registered_address"],
        "priority": 1,
        "preferred_sources": ["mca.gov.in"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    fields = {r["field_name"]: r for r in data["results"]}
    
    assert fields["legal_name"]["field_value"] == "WIPRO LIMITED"
    assert fields["company_status"]["field_value"] == "STRUCK OFF"
    assert fields["incorporation_date"]["field_value"] == "1945"
    assert "Sarjapur Road" in fields["registered_address"]["field_value"]

def test_genuine_registered_address_conflict_behavior(client_override):
    # Test 13: Genuine registered-address conflict behavior still works (retains exact values)
    mock_html = """
    <html>
    <body>
      <div>Registered Address: Doddakannelli, Sarjapur Road, Bengaluru 560035</div>
    </body>
    </html>
    """
    payload = {
        "task_id": "TASK-REG-07",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["registered_address"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert "Sarjapur Road" in data["results"][0]["field_value"]

def test_missing_evidence_has_confidence_zero(client_override):
    # Test 14: Missing evidence has confidence 0.0
    mock_html = "<html><body>Empty content</body></html>"
    payload = {
        "task_id": "TASK-REG-08",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["registered_address"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0

def test_browser_success_does_not_imply_evidence_success(client_override):
    # Test 15: Browser SUCCESS does not imply evidence SUCCESS (results have field_value=NOT_FOUND, confidence=0.0)
    mock_html = "<html><body>Google Search results for company website. None found.</body></html>"
    payload = {
        "task_id": "TASK-REG-09",
        "task_type": "WEBSITE_VERIFICATION",
        "target": "https://example.com",
        "objective": "Verify website claims",
        "required_fields": ["contact_address"],
        "priority": 2,
        "preferred_sources": ["company_website"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0


def test_gstin_relevance_hyphens(client_override):
    # Test that relevance check passes when GSTIN contains hyphens/formatting on the page
    mock_html = """
    <html>
    <head><title>WIPRO LIMITED</title></head>
    <body>
      <div>GSTIN of Taxpayer: 27-AAACW0387R-1Z6</div>
      <div>Legal Name: WIPRO LIMITED</div>
    </body>
    </html>
    """
    payload = {
        "task_id": "TASK-REG-10",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value=mock_html):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    assert data["results"][0]["field_value"] == "WIPRO LIMITED"


def test_direct_gst_portal_lookup_navigates_to_search_taxpayer_url(client_override):
    # Test that direct GST lookup resolves to the official services taxpayer page
    payload = {
        "task_id": "TASK-REG-11",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    
    # We trace that Playwright receives the official taxpayer page URL and fails due to CAPTCHA/human intervention
    mock_html = "<html><body>Please solve the captcha to proceed. recaptcha challenge.</body></html>"
    
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page") as mock_fetch:
        mock_fetch.return_value = mock_html
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
        # Verify the agent resolved to the taxpayer search page URL
        mock_fetch.assert_called_once_with("https://services.gst.gov.in/services/searchtp")
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0


def test_gst_never_resolves_to_generic_homepage(client_override):
    # Verify that the generic homepage is never resolved for GST taxpayer verification
    payload = {
        "task_id": "TASK-REG-12",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"]
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value="<html><body>captcha</body></html>") as mock_fetch:
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        # It must fetch the taxpayer search page, not the generic homepage
        assert mock_fetch.call_args[0][0] == "https://services.gst.gov.in/services/searchtp"


def test_captcha_blocks_when_no_fallback_configured(client_override):
    # Test that CAPTCHA results in unverified NOT_FOUND results when no fallback is configured
    payload = {
        "task_id": "TASK-REG-13",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"],
        "fallback_sources": []
    }
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", return_value="<html><body>solve the captcha below</body></html>"):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    assert len(data["results"]) == 1
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0


def test_captcha_triggers_fallback_when_fallback_configured(client_override):
    # Test that CAPTCHA on official source triggers fallback to registered third-party directory
    payload = {
        "task_id": "TASK-REG-14",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"],
        "fallback_sources": ["third_party"]
    }
    
    # Mock fetcher to return CAPTCHA for services.gst.gov.in, and valid HTML for third-party directory
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><body>solve the captcha below</body></html>"
        else:
            return "<html><head><title>WIPRO LIMITED</title></head><body>GSTIN: 27-AAACW0387R-1Z6</body></html>"
            
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    assert len(data["results"]) == 1
    assert data["results"][0]["field_value"] == "WIPRO LIMITED"
    assert data["results"][0]["confidence"] == 0.50


def test_fallback_evidence_must_contain_matching_info_before_accepted(client_override):
    # Test that fallback evidence must contain the actual target info (relevance matching)
    payload = {
        "task_id": "TASK-REG-15",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"],
        "fallback_sources": ["third_party"]
    }
    
    # Mock fallback to return page without matching GSTIN
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><body>solve the captcha below</body></html>"
        else:
            # Missing Wipro's GSTIN entirely, and contains over 50 words to trigger the relevance check
            return """
            <html>
            <head><title>Unrelated company</title></head>
            <body>
              No records shown here. We are a completely different business profile. 
              We specialize in gardening services and landscape design in Delhi. 
              Contact our sales representatives for additional details about our services. 
              We have been operating in the national capital region for over twenty years 
              and have served more than five hundred customers in the commercial and residential sectors.
            </body>
            </html>
            """
            
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0


def test_duckduckgo_homepage_cannot_become_evidence(client_override):
    # Verify that a DuckDuckGo search results page itself is never treated as evidence
    payload = {
        "task_id": "TASK-REG-16",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"],
        "fallback_sources": ["third_party"]
    }
    
    # Mock DuckDuckGo to return search engine page without result links but with search engine title
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><body>solve the captcha below</body></html>"
        else:
            return """
            <html>
            <head><title>DuckDuckGo - Protection. Privacy. Peace of mind.</title></head>
            <body>
              Welcome to DuckDuckGo search. Your search query was 27AAACW0387R1Z6.
              About protection and privacy settings on DuckDuckGo.
              No external links.
            </body>
            </html>
            """
            
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0


def test_duckduckgo_error_cannot_become_address(client_override):
    # Verify that DuckDuckGo error/privacy text cannot leak into address or status
    payload = {
        "task_id": "TASK-REG-17",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["registered_address"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"],
        "fallback_sources": ["third_party"]
    }
    
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><body>solve the captcha below</body></html>"
        else:
            return """
            <html>
            <head><title>DuckDuckGo Search</title></head>
            <body>
              DuckDuckGo privacy error: includes an anonymized error code 403.
              Security check failed. Please resolve.
            </body>
            </html>
            """
            
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"][0]["field_value"] == "NOT_FOUND"
    assert data["results"][0]["confidence"] == 0.0


def test_must_open_result_page_to_extract_evidence(client_override):
    # Verify that search result links are parsed and navigated to for actual evidence extraction
    payload = {
        "task_id": "TASK-REG-18",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"],
        "fallback_sources": ["third_party"]
    }
    
    visited_urls = []
    
    # We mock fetcher to return a real-looking DuckDuckGo search page with result links,
    # and a separate company profile page for the result link.
    def mock_fetcher(url):
        visited_urls.append(url)
        if "gst.gov.in" in url:
            return "<html><head><title>503 Service Unavailable</title></head><body>No records found on GST portal.</body></html>"
        elif "quickcompany.in" in url or "zaubacorp.com" in url or "third_party" in url:
            return """
            <html>
            <head><title>WIPRO LIMITED - Company Profile</title></head>
            <body>
              GSTIN of company: 27AAACW0387R1Z6.
              Legal Name: WIPRO LIMITED.
              This is the official profile page containing more than 50 words to pass relevance checks.
              Wipro is a major multinational corporation headquartered in Bengaluru, Karnataka, India.
              We provide information technology, consulting, and business process services.
            </body>
            </html>
            """
        return "<html><body>Empty</body></html>"
            
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    assert len(data["results"]) == 1
    
    # Verify evidence comes from registered public directory
    assert data["results"][0]["field_value"] == "WIPRO LIMITED"
    assert "quickcompany" in data["results"][0]["source_url"] or "zaubacorp" in data["results"][0]["source_url"]
    
    # Check visited URLs list contains the taxpayer search page and directory
    assert "https://services.gst.gov.in/services/searchtp" in visited_urls


def test_legal_name_normalization_strips_suffix(client_override):
    # Verify title normalization of known company page suffixes
    payload = {
        "task_id": "TASK-REG-19",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify legal name",
        "required_fields": ["legal_name"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"],
        "fallback_sources": ["third_party"]
    }
    
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><head><title>503 Service Unavailable</title></head><body>No records found on GST portal.</body></html>"
        elif "quickcompany.in" in url or "zaubacorp.com" in url or "third_party" in url:
            return """
            <html>
            <head><title>Wipro Limited - Company Profile, Shareholders, Directors</title></head>
            <body>
              Target company 27AAACW0387R1Z6.
              Company details page containing more than fifty words to satisfy page relevance check requirements.
              This is a standard corporate registry company details list.
            </body>
            </html>
            """
        return "<html><body>Empty</body></html>"
        
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    assert data["results"][0]["field_value"] == "Wipro Limited"
    assert data["results"][0]["evidence_basis"] == "Normalized company name from page title"


def test_gst_status_not_inferred_from_mca(client_override):
    # Verify company status is Active does NOT become gst_status = AVAILABLE
    payload = {
        "task_id": "TASK-REG-20",
        "task_type": "GST_VERIFICATION",
        "target": "27AAACW0387R1Z6",
        "objective": "Verify GST status",
        "required_fields": ["gst_status", "registered_address"],
        "priority": 1,
        "preferred_sources": ["gst.gov.in"],
        "fallback_sources": ["third_party"]
    }
    
    def mock_fetcher(url):
        if "gst.gov.in" in url:
            return "<html><head><title>503 Service Unavailable</title></head><body>No records found on GST portal.</body></html>"
        elif "quickcompany.in" in url or "zaubacorp.com" in url or "third_party" in url:
            return """
            <html>
            <head><title>Wipro Limited</title></head>
            <body>
              GSTIN of company: 27AAACW0387R1Z6.
              Company Status: Active.
              Registered Address: Dodda Kanneli, Sarjapur Road, Bengaluru, 560035.
              Corporate details and other company information page with more than fifty words for relevance checking.
            </body>
            </html>
            """
        return "<html><body>Empty</body></html>"
        
    with mock.patch("app.agents.browser.BrowserResearchAgent._fetch_page", side_effect=mock_fetcher):
        resp = client_override.post("/api/v1/test/browser-research", json=payload)
        
    assert resp.status_code == 200
    data = resp.json()
    assert data["browser_status"] == "SUCCESS"
    
    # gst_status must be UNAVAILABLE since there is no explicit GST status context on the page
    gst_res = [r for r in data["results"] if r["field_name"] == "gst_status"][0]
    assert gst_res["field_value"] == "UNAVAILABLE"
    assert gst_res["confidence"] == 0.0
    
    # registered_address must still be successfully extracted because it is explicitly present
    addr_res = [r for r in data["results"] if r["field_name"] == "registered_address"][0]
    assert "Dodda Kanneli" in addr_res["field_value"]
    assert addr_res["confidence"] == 0.5




