from __future__ import annotations

import abc
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app.graph.state import ResearchResult, ResearchTask


def sanitize_prompt_injection(text: str | None) -> str | None:
    if not text:
        return text
    patterns = [
        (r"(?i)\bignore\s+(?:previous|all|the|above|below)?\s*instructions\b", "[neutralized prompt injection instruction]"),
        (r"(?i)\bignore\s+rules\b", "[neutralized prompt injection rules]"),
        (r"(?i)\bignore\s+the\s+rules\b", "[neutralized prompt injection rules]"),
        (r"(?i)\bignore\s+previous\s+directives\b", "[neutralized prompt injection directive]"),
        (r"(?i)\byou\s+are\s+now\b", "[neutralized role-play instruction]"),
        (r"(?i)\bsystem\s+(?:prompt|instruction|directives)\b", "[neutralized system label]"),
        (r"(?i)\bdeveloper\s+instructions\b", "[neutralized system label]"),
    ]
    sanitized = text
    for pattern, replacement in patterns:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def extract_html_page_data(html: str) -> dict:
    if not html:
        return {"title": None, "text": ""}
    
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    title = clean_text(title_match.group(1)) if title_match else None
    title = sanitize_prompt_injection(title)

    body = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<style[^>]*>.*?</style>", " ", body, flags=re.IGNORECASE | re.DOTALL)
    body_newlines = re.sub(r"<(?:/div|/p|/h[1-6]|/tr|/li|br\s*/?|/td|/section|/article)>", "\n", body, flags=re.IGNORECASE)
    plain_text = re.sub(r"<[^>]+>", " ", body_newlines)
    cleaned_lines = [clean_text(l) for l in plain_text.split("\n")]
    text = "\n".join(l for l in cleaned_lines if l)
    text = sanitize_prompt_injection(text)

    return {
        "title": title,
        "text": text,
    }


def detect_bot_or_captcha(html: str | None) -> str | None:
    if not html:
        return None

    html_lower = html.lower()

    # 1. Obvious Bot Challenges
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title_text = title_match.group(1).lower()
        if any(w in title_text for w in ["captcha", "robot verification", "verify you are human", "attention required", "security check"]):
            return "CAPTCHA"

    captcha_patterns = [
        r"recaptcha",
        r"hcaptcha",
        r"g-recaptcha",
        r"bot verification",
        r"verify you are human",
        r"robot check",
        r"prove you're not a robot",
        r"please solve the captcha",
        r"solve the captcha",
        r"security check to proceed",
        r"complete the captcha",
        r"cf-browser-verification",
        r"cf-im-under-attack",
        r"distribute captcha",
        r"enter captcha",
        r"captcha code",
        r"captcha image",
        r"name=['\"]captcha['\"]",
        r"id=['\"]captcha['\"]",
    ]
    for pattern in captcha_patterns:
        if pattern in {"recaptcha", "hcaptcha", "g-recaptcha"}:
            if pattern in html_lower:
                return "CAPTCHA"
        else:
            if re.search(pattern, html_lower):
                return "CAPTCHA"

    # 2. OTP Check
    otp_patterns = [
        r"enter\s+(?:the\s+)?(?:[0-9]-digit\s+)?otp",
        r"please\s+enter\s+(?:the\s+)?otp",
        r"enter\s+(?:the\s+)?one-time\s+password",
        r"enter\s+(?:the\s+)?one\s+time\s+password",
        r"verification\s+code\s+sent\s+to\s+your",
        r"enter\s+verification\s+code\s+sent",
        r"two-factor\s+authentication\s+required",
        r"two-factor\s+authentication\s+code",
        r"<input[^>]+name=['\"](?:otp|otp_code|verification_code)['\"]",
    ]
    for pattern in otp_patterns:
        if re.search(pattern, html_lower):
            return "OTP"

    # 3. Login Check
    login_patterns = [
        r"login required",
        r"please log in",
        r"please sign in",
        r"sign in to your account",
        r"authentication required",
        r"member login",
        r"sign in to proceed",
        r"log in to continue",
        r"sign in to continue",
    ]
    for pattern in login_patterns:
        if re.search(r"\b" + re.escape(pattern) + r"\b", html_lower):
            return "LOGIN_REQUIRED"

    return None


def is_failed_or_blocked_response(html: str | None, target: str) -> str | None:
    if not html or not html.strip():
        return "EMPTY_RESPONSE"

    intervention = detect_bot_or_captcha(html)
    if intervention:
        return "BLOCKED_OR_ERROR"

    html_lower = html.lower()

    blocked_patterns = [
        "access denied",
        "403 forbidden",
        "403 error",
        "404 not found",
        "404 error",
        "page not found",
        "401 unauthorized",
        "503 service unavailable",
        "502 bad gateway",
        "500 internal server error",
        "attention required! | cloudflare",
        "cf-browser-verification",
        "error code 1020",
        "requested url was rejected",
        "security check to proceed",
        "duckduckgo privacy error",
        "anonymized error code",
        "protection. privacy. peace of mind",
    ]
    for pattern in blocked_patterns:
        if pattern in html_lower:
            return "BLOCKED_OR_ERROR"

    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title_text = title_match.group(1).lower()
        if any(kw in title_text for kw in ["access denied", "forbidden", "attention required", "error", "unauthorized", "404", "not found", "page not found", "duckduckgo", "bing search", "google search", "yahoo search"]):
            return "BLOCKED_OR_ERROR"

    no_results_patterns = [
        "no results found",
        "0 results",
        "no records found",
        "no data found",
        "record not found",
        "invalid gstin",
        "invalid cin",
        "invalid format",
    ]
    for pattern in no_results_patterns:
        if pattern in html_lower:
            return "NO_RESULTS"

    # Relevance check
    page_data = extract_html_page_data(html)
    page_text = page_data.get("text") or ""
    page_title = page_data.get("title") or ""
    words = page_text.split()
    if len(words) == 0:
        return "EMPTY_RESPONSE"

    incompatible_sectors = [
        "hotel", "resort", "inn", "suites", "motel", "pharma", "pharmaceutical",
        "hospital", "clinic", "coaching", "academy", "classes", "tuition",
        "huel", "adult", "casino", "dating", "escort"
    ]
    target_lower = str(target).lower().strip()
    title_lower = page_title.lower()
    for kw in incompatible_sectors:
        if kw in title_lower and kw not in target_lower:
            return "IRRELEVANT_SECTOR"
        if kw in page_text.lower()[:300] and kw not in target_lower and f" {kw} " in f" {page_text.lower()[:300]} ":
            return "IRRELEVANT_SECTOR"

    if len(words) >= 15:
        # Gateway portal check (Government / Authority search portals & directories)
        portal_indicators = [
            "search taxpayer", "goods and services tax", "gst portal", "gst services",
            "ministry of corporate affairs", "mca services", "company master data",
            "employees' provident fund", "epfo", "epfindia", "electronic challan",
            "income tax department", "quickcompany", "tofler", "zauba corp", "instafinancials"
        ]
        if any(ind in page_text.lower() or ind in title_lower for ind in portal_indicators):
            return None

        # Exact CIN or GSTIN match in text or title
        cin_match = re.search(r"\b([ul][0-9]{5}[a-z]{2}[0-9]{4}[a-z]{3}[0-9]{6})\b", target_lower)
        gstin_match = re.search(r"\b([0-9]{2}[a-z]{5}[0-9]{4}[a-z]{1}[1-9a-z]{1}z[0-9a-z]{1})\b", target_lower)
        if cin_match and (cin_match.group(1) in page_text.lower() or cin_match.group(1) in title_lower):
            return None
        if gstin_match and (gstin_match.group(1) in page_text.lower() or gstin_match.group(1) in title_lower):
            return None

        # Check for valid business / corporate registry signals
        has_business_signals = any(sig in page_text.lower() for sig in ["gst status", "company status", "mca status", "legal name", "cin", "gstin", "private limited", "limited", "pvt ltd", "registered address", "incorporation date"])
        if has_business_signals and (cin_match or gstin_match):
            return None

        if is_url(target_lower) or "." in target_lower or "/" in target_lower:
            domain = target_lower
            if "://" in domain:
                try:
                    parsed = urlparse(domain)
                    domain = parsed.netloc or domain
                except Exception:
                    pass
            domain = domain.replace("www.", "").strip()
            domain_prefix = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
            normalized_text = re.sub(r"[^a-z0-9]", "", page_text.lower())
            if len(domain_prefix) > 2 and domain_prefix not in normalized_text and domain_prefix not in page_title.lower():
                return "IRRELEVANT_CONTENT"
        elif any(c.isdigit() for c in target_lower) and len(target_lower) > 5 and " " not in target_lower.strip():
            normalized_target = re.sub(r"[^a-z0-9]", "", target_lower)
            normalized_text = re.sub(r"[^a-z0-9]", "", page_text.lower())
            if normalized_target not in normalized_text and not has_business_signals:
                return "IRRELEVANT_CONTENT"
        else:
            stop_words = {"limited", "pvt", "ltd", "private", "corporation", "corp", "inc", "incorporated", "co", "company", "and", "the", "official", "website", "registration", "establishment", "search", "portal", "mca", "epfo"}
            target_words = [w for w in re.findall(r"\b\w+\b", target_lower) if w not in stop_words and len(w) > 2]
            if target_words:
                matched_count = sum(1 for word in target_words if word in page_text.lower() or word in title_lower)
                if len(target_words) > 1:
                    if matched_count < min(2, len(target_words)) or (matched_count / len(target_words)) < 0.50:
                        return "IRRELEVANT_CONTENT"
                else:
                    if matched_count < 1:
                        return "IRRELEVANT_CONTENT"
            else:
                if target_lower not in page_text.lower() and target_lower not in title_lower:
                    return "IRRELEVANT_CONTENT"

    return None


def is_url(value: str) -> bool:
    if not value or not isinstance(value, str):
        return False
    candidate = value.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    return bool(parsed.scheme in {"http", "https"} and parsed.netloc and "." in parsed.netloc)


def http_fetch_direct(url: str, timeout: int = 10) -> str:
    """
    Direct resilient HTTP fetch using urllib without browser overhead.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid or unsupported research URL: {url}")

    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        content_bytes = response.read()
        return content_bytes.decode("utf-8", errors="replace")


def extract_address_from_text(text: str | None) -> str:
    if not text:
        return "NOT_FOUND"
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    address_prefixes = [
        "registered address of", "registered office address of",
        "principal place of business", "principal place",
        "registered office address", "registered office", "registered address",
        "corporate office", "office address", "contact address", "address"
    ]
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(neg in line_lower for neg in ["no address", "address not", "not published", "not available", "unknown address", "same address", "similar address"]):
            continue
        for prefix in address_prefixes:
            if prefix in line_lower:
                match = re.search(r"\b" + re.escape(prefix) + r"\s*(?:is|:|-)?\s*(.*)", line, re.IGNORECASE)
                if match and len(match.group(1).strip()) > 5:
                    content = match.group(1).strip()
                    content = re.sub(r"^(?:[A-Za-z0-9\s.,&()/-]+\s+is\s+|is\s+|at\s+|is\s+at\s+)", "", content, flags=re.IGNORECASE).strip()
                    content = re.split(r"(?i)\s+(?:business\s*activity|activity|gst\s*status|gstin|cin|status|incorporation|registration\s*date|contact|phone|email|website|date\s*of\s*incorporation|pan)\b", content)[0].strip()
                    if "." in content and len(content.split(".")[0]) > 8:
                        content = content.split(".")[0].strip()
                    if content and len(content) > 5 and not any(k in content.lower() for k in ["search", "company", "director", "menu"]):
                        return content
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if len(next_line) > 10 and not any(k in next_line.lower() for k in ["search", "menu", "home", "company", "director"]):
                        return next_line

    indian_states = {"maharashtra", "karnataka", "delhi", "tamil nadu", "telangana", "gujarat", "west bengal", "haryana", "uttar pradesh", "mumbai", "bengaluru", "bangalore", "chennai", "hyderabad", "kolkata", "pune", "gurgaon", "noida"}
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(neg in line_lower for neg in ["same address", "similar address", "search", "menu"]):
            continue
        if re.search(r"\b\d{6}\b", line) and any(state in line_lower for state in indian_states):
            clean_line = re.sub(r"^(?:at\s+|is\s+at\s+|registered\s+address\s+of\s+[A-Za-z0-9\s.,&()/-]+\s+is|principal\s+place\s+of\s+business|registered\s+address|address|office)\s*[:\-]?", "", line, flags=re.IGNORECASE).strip()
            if len(clean_line) > 10:
                return clean_line
            addr_block = [lines[j] for j in range(max(0, i - 2), i + 1)]
            return " | ".join(addr_block)

    return "NOT_FOUND"


def extract_date_from_text(text: str | None) -> str:
    if not text:
        return "NOT_FOUND"
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines:
        line_lower = line.lower()
        if any(kw in line_lower for kw in ["incorporated", "incorporation", "established", "founded", "estd"]):
            match = re.search(r"\b(19\d{2}|20\d{2})\b", line)
            if match:
                return match.group(1)
            match_date = re.search(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b|\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", line)
            if match_date:
                return match_date.group(0)
    return "NOT_FOUND"


def extract_business_activity_from_text(text: str | None) -> str:
    if not text:
        return "NOT_FOUND"
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    invalid_activities = {
        "code", "activities", "business activity", "activity", "nic code", "class",
        "category", "n/a", "na", "error", "none", "not found", "unavailable", "null",
        "details", "description", "industry", "industrial class", "type", "sector"
    }
    for line in lines:
        match = re.search(
            r"(?:principal\s+business\s+activity|business\s+activity(?:\s+description)?|nature\s+of\s+business(?:\s+activities)?|nic\s+(?:code\s+)?description|activity\s+description|industrial\s+class)\s*[:\-]?\s*(.+)",
            line,
            re.IGNORECASE
        )
        if match:
            candidate = match.group(1).strip()
            # Clean off trailing labels
            candidate = re.split(r"(?i)\s+(?:cin|gstin|company\s+status|status|date\s+of|registered\s+office)\b", candidate)[0].strip()
            cand_clean = re.sub(r"^[:\-–—\s]+|[.:\-–—\s]+$", "", candidate).strip()
            if cand_clean.lower() not in invalid_activities and len(cand_clean) >= 4 and not re.match(r"^\d+$", cand_clean):
                return cand_clean

    return "NOT_FOUND"


def extract_status_from_text(text: str | None) -> str:
    if not text:
        return "NOT_FOUND"
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    valid_statuses = [
        ("ACTIVE", ["active"]),
        ("INACTIVE", ["inactive"]),
        ("CANCELLED", ["cancelled", "canceled"]),
        ("SUSPENDED", ["suspended"]),
        ("STRIKE OFF", ["strike off", "struck off", "strikeoff"]),
        ("AMALGAMATED", ["amalgamated"]),
        ("UNDER LIQUIDATION", ["under liquidation", "in liquidation"]),
        ("DORMANT", ["dormant"]),
        ("DISSOLVED", ["dissolved"]),
    ]
    for line in lines:
        line_lower = line.lower()
        if any(kw in line_lower for kw in ["status", "company status", "gst status", "registration status"]):
            for canonical, keywords in valid_statuses:
                for kw in keywords:
                    if re.search(r"\b" + re.escape(kw) + r"\b", line_lower):
                        return canonical
    return "NOT_FOUND"


def clean_legal_name_candidate(name_candidate: str | None) -> str | None:
    if not name_candidate:
        return None
    import html as html_lib
    name = html_lib.unescape(name_candidate.strip())
    name = re.sub(r"<[^>]+>", " ", name)
    name = " ".join(name.split())

    suffix_patterns = [
        r"(?i)\s+Financials\s*\|\s*Company\s+Details.*$",
        r"(?i)\s*[-|–—:]\s*Company\s+Details.*$",
        r"(?i)\s*[-|–—:]\s*Company\s+Profile.*$",
        r"(?i)\s*[-|–—:]\s*Profile.*$",
        r"(?i)\s*[-|–—:]\s*Overview.*$",
        r"(?i)\s*[-|–—:]\s*MCA\s+Details.*$",
        r"(?i)\s*[-|–—:]\s*Financials.*$",
        r"(?i)\s*[-|–—:]\s*Company\s+Registration.*$",
        r"(?i)\s*[-|–—:]\s*Registration.*$",
        r"(?i)\s*[-|–—:]\s*Master\s+Data.*$",
        r"(?i)\s*[-|–—:]\s*Corporate\s+Identification.*$",
        r"(?i)\s*[-|–—:]\s*CIN.*$",
        r"(?i)\s*[-|–—:]\s*GSTIN.*$",
        r"(?i)\s*[-|–—:]\s*Zauba\s*Corp.*$",
        r"(?i)\s*[-|–—:]\s*Tofler.*$",
        r"(?i)\s*[-|–—:]\s*QuickCompany.*$",
        r"(?i)\s*[-|–—:]\s*InstaFinancials.*$",
        r"(?i)\s*[-|–—:]\s*Shareholders.*$",
        r"(?i)\s*[-|–—:]\s*Directors.*$",
        r"(?i)\s*[-|–—:]\s*Official\s+Site.*$",
        r"(?i)\s*[-|–—:]\s*Official\s+Website.*$",
        r"(?i)\s*[-|–—:]\s*Home.*$",
        r"(?i)\s*[-|–—:]\s*About\s+Us.*$",
        r"(?i)\s*,\s*Shareholders.*$",
        r"(?i)\s*,\s*Directors.*$",
        r"(?i)\s+at\s+DuckDuckGo.*$",
        r"(?i)\s*[-|–—:]\s*Google\s+Search.*$",
        r"(?i)\s*[-|–—:]\s*DuckDuckGo.*$",
        r"(?i)\s*[-|–—:]\s*Bing.*$",
    ]
    for pat in suffix_patterns:
        name = re.sub(pat, "", name).strip()

    # Strip trailing location contamination (e.g. "TATA CONSULTANCY SERVICES LIMITED in maharashtra")
    name = re.sub(r"(?i)\s+\bin\s+[A-Za-z]+(?:\s+[A-Za-z]+)?$", "", name).strip()
    name = re.sub(r"(?i)\s+(?:mca\s+company\s+registration|epfo\s+establishment|official\s+website|company\s+registration|master\s+data)\b.*$", "", name).strip()

    KNOWN_SLOGANS = [
        "leadership with trust", "where quality matters", "online store", "online electronic",
        "shopping store", "shopping online", "best deals", "leading provider", "where quality",
        "welcome to", "your trusted", "buy online", "lowest prices", "customer support",
        "trust and value", "powering the future", "touching lives", "improving the quality of life",
        "delivering excellence", "world class solutions", "shaping tomorrow", "innovating for growth",
        "trademarks and logos", "s and logos appearing", "logos appearing on", "all rights reserved"
    ]
    name_lower = name.lower()
    for slogan in KNOWN_SLOGANS:
        if slogan in name_lower:
            name = re.split(re.escape(slogan), name, flags=re.IGNORECASE)[0].strip()
            name = re.sub(r"[.\-–—:|/]+$", "", name).strip()

    name = re.sub(r"[.\-–—:|/,\s]+$", "", name).strip()
    name_lower = name.lower()

    reject_phrases = {
        "welcome", "home", "login", "index", "the group", "leadership", "trust",
        "the tata group", "welcome - online", "404 not found", "page not found",
        "not found", "access denied", "forbidden", "duckduckgo", "search results", "search",
        "of business", "nature of business", "business activity", "registered office",
        "s and logos appearing on the site", "trademarks and logos appearing on the site",
        "terms of use", "privacy policy", "all rights reserved", "something went wrong", "error"
    }

    portal_titles = [
        "goods & services tax", "goods and services tax", "search taxpayer",
        "ministry of corporate affairs", "mca services", "company master data",
        "employees' provident fund", "epfindia", "gst portal", "mca portal", "epfo portal",
        "search results", "companies matching", "search companies", "results for"
    ]

    if (
        len(name) < 3
        or name_lower.startswith(("welcome", "home", "login", "index", "about us", "online store", "online shopping", "search results", "search -", "search for", "companies matching", "404", "503", "502", "500", "403", "401"))
        or name_lower in reject_phrases
        or any(phrase in name_lower for phrase in ["s and logos appearing", "trademarks and logos", "something went wrong", "access denied", "page not found"])
        or any(se in name_lower for se in ["duckduckgo", "bing search", "google search", "yahoo search"])
        or any(pt in name_lower for pt in portal_titles)
    ):
        return None

    return name


def classify_entity_relationship(target: str, domain: str, page_title: str, page_text: str) -> str:
    target_lower = (target or "").lower().strip()
    title_lower = (page_title or "").lower().strip()
    text_lower = (page_text or "").lower()[:1500]

    stop_words = {
        "pvt", "ltd", "limited", "private", "llp", "corp", "inc", "co", "company",
        "official", "website", "the", "and", "in", "india", "services", "solutions", "group",
        "http", "https", "www", "com", "org", "net", "io", "gov", "edu", "html", "htm"
    }
    target_tokens = [t for t in re.findall(r"\b[a-z0-9]+\b", target_lower) if t not in stop_words and len(t) > 2]

    domain_clean = re.sub(r"^www\.", "", (domain or "").lower())
    clean_target_url = re.sub(r"^www\.", "", target_lower.replace("https://", "").replace("http://", "").rstrip("/"))
    if domain_clean and (domain_clean in target_lower or clean_target_url in domain_clean or clean_target_url == domain_clean or (len(target_tokens) == 1 and target_tokens[0] in domain_clean)):
        return "TARGET_ENTITY"

    gstin_match = re.search(r"\b([0-9]{2}[a-z]{5}[0-9]{4}[a-z]{1}[1-9a-z]{1}z[0-9a-z]{1})\b", target_lower)
    cin_match = re.search(r"\b([ul][0-9]{5}[a-z]{2}[0-9]{4}[a-z]{3}[0-9]{6})\b", target_lower)
    if (gstin_match and gstin_match.group(1) in text_lower) or (cin_match and cin_match.group(1) in text_lower):
        return "TARGET_ENTITY"

    if not target_tokens:
        return "TARGET_ENTITY"

    matched_in_title = sum(1 for t in target_tokens if t in title_lower)
    matched_in_text = sum(1 for t in target_tokens if t in text_lower)

    is_group_title = bool(re.search(r"\b(?:the\s+[a-z0-9]+\s+group|group\s+of\s+companies|holding\s+company|conglomerate)\b", title_lower))
    if is_group_title and len(target_tokens) > 1 and matched_in_title < len(target_tokens):
        return "PARENT_ENTITY"

    if matched_in_title == len(target_tokens) or (len(target_tokens) > 1 and matched_in_title >= len(target_tokens) - 1):
        return "TARGET_ENTITY"

    if matched_in_text >= len(target_tokens):
        return "TARGET_ENTITY"

    if len(target_tokens) > 1 and (matched_in_title == 1 or matched_in_text == 1):
        return "PARENT_ENTITY"

    if matched_in_text > 0 or matched_in_title > 0:
        return "RELATED_ENTITY"

    return "UNRELATED"


def score_candidate_url(res_url: str, target: str, task_type: str) -> tuple[float, str, str]:
    if not res_url or not res_url.startswith(("http://", "https://")):
        return 0.0, "Invalid URL scheme", "UNRELATED"
    try:
        parsed = urlparse(res_url)
        domain = (parsed.netloc or "").lower().replace("www.", "")
        path = (parsed.path or "").lower()
    except Exception:
        return 0.0, "URL parse exception", "UNRELATED"

    target_lower = target.lower().strip()

    if any(d in domain for d in ["duckduckgo.com", "bing.com", "google.com", "yahoo.com", "youtube.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "pinterest.com"]):
        return 0.0, "Search engine or social media destination (rejected)", "UNRELATED"

    incompatible_keywords = [
        "hotel", "resort", "inn", "suites", "motel", "pharma", "pharmaceutical",
        "pharmacy", "drugs", "hospital", "clinic", "coaching", "academy",
        "classes", "tuition", "huel", "adult", "casino", "dating", "escort",
        "porn", "xxx", "sex", "cricinfo", "cricket", "imdb", "movie", "cinema", "football"
    ]
    for kw in incompatible_keywords:
        if (kw in domain or f"/{kw}" in path) and kw not in target_lower:
            return 0.0, f"Incompatible sector '{kw}' in domain/path", "UNRELATED"

    gstin_match = re.search(r"\b([0-9]{2}[a-z]{5}[0-9]{4}[a-z]{1}[1-9a-z]{1}z[0-9a-z]{1})\b", target_lower)
    cin_match = re.search(r"\b([ul][0-9]{5}[a-z]{2}[0-9]{4}[a-z]{3}[0-9]{6})\b", target_lower)
    if gstin_match and gstin_match.group(1) in res_url.lower():
        return 1.0, "Exact GSTIN matched in candidate URL", "TARGET_ENTITY"
    if cin_match and cin_match.group(1) in res_url.lower():
        return 1.0, "Exact CIN matched in candidate URL", "TARGET_ENTITY"

    stop_words = {
        "pvt", "ltd", "limited", "private", "llp", "corp", "inc", "co", "company",
        "official", "website", "the", "and", "in", "india", "group",
        "http", "https", "www", "com", "org", "net", "io", "gov", "edu", "html", "htm"
    }
    target_tokens = [t for t in re.findall(r"\b[a-z0-9]+\b", target_lower) if t not in stop_words and len(t) > 2]
    acronym_words = [t for t in re.findall(r"\b[a-z0-9]+\b", target_lower) if t not in {"pvt", "ltd", "limited", "private", "llp", "the", "and", "in", "co", "inc", "group"}]
    acronym = "".join([w[0] for w in acronym_words]) if len(acronym_words) >= 2 else ""

    domain_clean = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
    path_clean = re.sub(r"[^a-z0-9]", "", path)

    if not target_tokens:
        return 0.50, "Neutral candidate (no distinctive tokens in target)", "UNKNOWN"

    matched_tokens = [t for t in target_tokens if t in domain_clean or t in path_clean]
    overlap_ratio = len(matched_tokens) / len(target_tokens)
    if acronym and (acronym == domain_clean or f"/{acronym}" in path or f"-{acronym}" in domain):
        overlap_ratio = max(overlap_ratio, 0.95)

    is_reputable_directory = any(d in domain for d in ["zaubacorp.com", "tofler.in", "instafinancials.com", "quickcompany.in"])

    if task_type == "WEBSITE_VERIFICATION":
        aggregators = [
            "zaubacorp.com", "tofler.in", "quickcompany.in", "instafinancials.com",
            "indiafilings.com", "company360.in", "economictimes.indiatimes.com",
            "indiamart.com", "tradeindia.com", "justdial.com", "fundoodata.com",
            "instahyre.com", "ambitionbox.com", "glassdoor.com", "crunchbase.com",
            "mca.gov.in", "gst.gov.in", "epfindia.gov.in", "incometax.gov.in",
            "wikipedia.org", "github.com"
        ]
        if any(d in domain for d in aggregators):
            return 0.0, "Directory / Registry site cannot be official company website", "UNRELATED"

        if overlap_ratio >= 0.60:
            return 0.90, f"Strong token overlap ({overlap_ratio:.2f}) for official website", "TARGET_ENTITY"
        elif overlap_ratio > 0.0 and len(target_tokens) > 1 and len(matched_tokens) == 1:
            return 0.35, f"Single token overlap ({matched_tokens[0]}) suggests parent or group entity", "PARENT_ENTITY"
        elif overlap_ratio > 0:
            return 0.50, f"Partial token overlap ({overlap_ratio:.2f})", "RELATED_ENTITY"
        else:
            return 0.0, "No token overlap with target business name", "UNRELATED"

    elif task_type == "THIRD_PARTY_RESEARCH":
        if is_reputable_directory:
            if overlap_ratio >= 0.50:
                return 0.95, f"Reputable registry with token overlap ({overlap_ratio:.2f})", "TARGET_ENTITY"
            elif overlap_ratio > 0.0:
                return 0.50, f"Reputable registry with partial token overlap ({overlap_ratio:.2f})", "RELATED_ENTITY"
            return 0.0, "No token overlap on corporate registry", "UNRELATED"
        if overlap_ratio >= 0.50:
            return 0.75, f"Third-party site with token overlap ({overlap_ratio:.2f})", "TARGET_ENTITY"
        elif overlap_ratio > 0.0:
            return 0.40, f"Third-party site with partial token overlap ({overlap_ratio:.2f})", "RELATED_ENTITY"
        return 0.0, "No token overlap for third-party source", "UNRELATED"

    else:
        if overlap_ratio >= 0.60:
            return 0.85, f"Strong token overlap ({overlap_ratio:.2f})", "TARGET_ENTITY"
        elif overlap_ratio >= 0.30:
            return 0.60, f"Moderate token overlap ({overlap_ratio:.2f})", "RELATED_ENTITY"
        else:
            return 0.0, f"Low or no token overlap ({overlap_ratio:.2f})", "UNRELATED"


class BaseResearchProvider(abc.ABC):
    """
    Common Research Provider interface.
    """
    provider_name: str = "BaseProvider"
    supported_task_types: set[str] = set()

    def __init__(self, fetcher: Optional[Callable[[str], str]] = None):
        self.fetcher = fetcher or http_fetch_direct

    @abc.abstractmethod
    def can_handle(self, task: ResearchTask) -> bool:
        """Determines if this provider can execute the given task."""
        return task.task_type in self.supported_task_types

    @abc.abstractmethod
    def research(
        self,
        task: ResearchTask,
        investigation_id: Optional[uuid.UUID] = None,
    ) -> list[ResearchResult]:
        """Executes the research task and returns structured results."""
        pass

    def execute(
        self,
        task: ResearchTask,
        investigation_id: Optional[uuid.UUID] = None,
        fetcher: Optional[Callable[[str], str]] = None,
    ) -> list[ResearchResult]:
        """Convenience execution method compatible with agent runner and test callers."""
        if fetcher is not None:
            self.fetcher = fetcher
        return self.research(task, investigation_id=investigation_id)
