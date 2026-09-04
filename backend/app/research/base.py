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


def is_address_like(addr: str | None) -> bool:
    if not addr or not isinstance(addr, str):
        return False
    val = addr.strip()
    if len(val) < 8:
        return False

    val_lower = val.lower()

    # 1. Reject invalid placeholders, error phrases, patent text, and filing metadata
    invalid_keywords = {
        "not_found", "unavailable", "unknown", "error", "blocked", "irrelevant",
        "source_unavailable", "captcha_required", "not applicable", "n/a", "na",
        "none", "null", "something went wrong", "access denied", "403 forbidden",
        "page not found", "404 not found", "503 service unavailable", "502 bad gateway",
        "500 internal server error", "empty_response",
    }
    if val_lower in invalid_keywords:
        return False

    if any(k in val_lower for k in [
        "patent number", "patent journal", "patent watch", "trademark", "computer science",
        "amendment", "registration core fields", "core fields", "director details",
        "signatory details", "shareholder", "balance sheet", "din :", "din:", "pan :", "pan:"
    ]):
        return False

    # 2. Reject section headers, field labels, navigation, UI text, and business activity labels
    section_headers_and_labels = [
        "nature of business activities", "nature of business", "business activity",
        "business activities", "principal business activity", "main activity",
        "activity description", "principal place of business", "principal place",
        "registered office address", "registered office", "registered address",
        "establishment address", "corporate office", "office address", "contact details", "contact us",
        "about us", "terms of service", "privacy policy", "copyright",
        "all rights reserved", "company master data", "taxpayer details",
        "filing status", "director details", "directors / signatory details",
        "charges / balance sheet", "search taxpayer", "search company",
        "search results", "navigation", "home / about", "view financials",
        "financial details", "status: active", "gstin / uin", "cin / llpin",
        "trade name", "legal name", "company status", "date of incorporation",
        "jurisdiction", "constitution of business", "administrative office",
    ]
    for header in section_headers_and_labels:
        if val_lower == header or val_lower == f"{header}:" or val_lower.startswith(f"{header} -") or val_lower.startswith(f"{header}:"):
            return False

    # 3. Reject activity taxonomy descriptions
    activity_starters = [
        "software publishing", "consultancy and supply", "other service activities",
        "manufacture of", "wholesale of", "retail trade", "wholesale trade",
        "legal activities", "accounting, bookkeeping", "management consultancy",
        "architectural and engineering", "advertising and market", "publishing of",
        "telecommunications", "financial service activities", "insurance, reinsurance",
        "construction of buildings", "civil engineering", "specialised construction",
        "food and beverage service", "accommodation", "information service activities"
    ]
    for act in activity_starters:
        if val_lower.startswith(act) or val_lower == act:
            return False

    # 4. Check for genuine address structure:
    has_pincode = bool(re.search(r"\b[1-9][0-9]{5}\b", val) or re.search(r"\b\d{5}(?:-\d{4})?\b", val))

    address_structure_keywords = [
        r"\b(?:floor|flr|ground\s+floor|first\s+floor|second\s+floor|third\s+floor|4th\s+floor|5th\s+floor|6th\s+floor|7th\s+floor|8th\s+floor|9th\s+floor|10th\s+floor)\b",
        r"\b(?:building|bldg|tower|towers|park|plaza|court|heights|house|mansion|bhavan|bhawan)\b",
        r"\b(?:road|rd|street|st|marg|lane|path|avenue|ave|drive|crescent|way|circle|chowk|bypass|highway)\b",
        r"\b(?:plot|no\.?|house\s+no|h\.?no|flat|unit|suite|office|off\.?|room|cabin|premises|block|blk|sector|sec|phase|ph)\b",
        r"\b(?:nagar|colony|enclave|complex|estate|vihar|puram|wadi|peth|layout|industrial\s+area|ind\s+area|midc|gidc|riico|sez)\b",
        r"\b(?:point|hub|centre|center|square|sq|cross|main|junction|opposite|opp|near|nr|behind|adj)\b",
    ]
    has_address_keyword = any(bool(re.search(pat, val_lower)) for pat in address_structure_keywords)

    indian_regions = {
        "maharashtra", "karnataka", "delhi", "new delhi", "tamil nadu", "telangana", "gujarat",
        "west bengal", "haryana", "uttar pradesh", "mumbai", "bengaluru", "bangalore", "chennai",
        "hyderabad", "kolkata", "pune", "gurgaon", "gurugram", "noida", "ahmedabad", "jaipur",
        "chandigarh", "kochi", "coimbatore", "indore", "india", "bharat"
    }
    has_region = any(r in val_lower for r in indian_regions)

    # Valid genuine address must have (PIN code AND (address keyword OR region)) OR (address keyword AND region AND length >= 15)
    if has_pincode and (has_address_keyword or has_region):
        return True
    if has_address_keyword and has_region and len(val) >= 15:
        return True
    if has_pincode and len(val) >= 20 and "," in val:
        return True

    return False


def is_valid_legal_name(name: str | None) -> bool:
    if not name or not isinstance(name, str):
        return False
    val = name.strip()
    if not val or len(val) > 150:
        return False

    val_lower = val.lower()

    # Reject if contains " in " (e.g. "COMPANY NAME in Maharashtra, India")
    if re.search(r"\s+\bin\s+.*$", val_lower):
        return False

    # Reject verbose directory/registry titles that carry registration,
    # identifier, age or date metadata alongside the name — the name should have
    # been parsed out of these before validation. Structural check, no source or
    # company names.
    if (
        re.search(r"\b[ul][0-9]{5}[a-z]{2}[0-9]{4}[a-z]{3}[0-9]{6}\b", val_lower)        # CIN
        or re.search(r"\b[0-9]{2}[a-z]{5}[0-9]{4}[a-z][0-9a-z]z[0-9a-z]\b", val_lower)   # GSTIN
        or re.search(r"\bhaving\s+(?:cin|gstin|llpin|pan|din|tan)\b", val_lower)
        or re.search(r"\bis\s+\d+\s+(?:year|yr|month|mo|week|wk|day)s?\b", val_lower)
        or re.search(r"\b(?:date|year)\s+of\s+(?:incorporation|registration|establishment)\b", val_lower)
        or re.search(r"\b(?:incorporated|registered)\s+(?:on|in|as|since|under|with)\s+\S", val_lower)
    ):
        return False

    # Reject placeholders and headers
    invalid_keywords = {
        "not_found", "unavailable", "unknown", "error", "blocked", "irrelevant",
        "source_unavailable", "captcha_required", "not applicable", "n/a", "na",
        "none", "null", "something went wrong", "access denied", "page not found",
        "search results", "results for", "duckduckgo", "google search", "bing search",
        "nature of business activities", "nature of business", "business activity",
        "registered office", "registered address", "contact us", "about us", "terms of use",
        "privacy policy", "all rights reserved"
    }
    if val_lower in invalid_keywords:
        return False

    portal_prefixes = (
        "welcome", "home", "login", "index", "search", "companies matching", "404", "503", "502", "500", "403"
    )
    if val_lower.startswith(portal_prefixes):
        return False

    indian_regions_strict = {
        "maharashtra", "karnataka", "delhi", "new delhi", "tamil nadu", "telangana", "gujarat",
        "west bengal", "haryana", "uttar pradesh", "mumbai", "bengaluru", "bangalore", "chennai",
        "hyderabad", "kolkata", "pune", "gurgaon", "gurugram", "noida", "india", "bharat"
    }
    if val_lower in indian_regions_strict or val_lower.replace(",", " ").strip() in indian_regions_strict:
        return False

    return True


# Closed-class English connectives. A genuine activity *description* almost
# always contains at least one of these ("manufacture OF ...", "trade AND ...");
# a navigation bar ("Home Products Services Careers Contact") does not. This is a
# grammatical signal, not a phrase blacklist.
_ACTIVITY_FUNCTION_WORDS = {
    "of", "and", "the", "in", "for", "with", "to", "or", "on", "at", "by",
    "from", "a", "an", "&", "as", "including", "related",
}

# Industry-classification (NIC / ISIC / SIC) dictionary text has a characteristic
# shape: a class label followed by a *bracketed or parenthesised gloss* that
# enumerates or exemplifies what the class covers, e.g.
#   "Printing [Includes printing of newspapers, books, periodicals ...]"
#   "Other computer related activities [for example maintenance of websites ...]"
#   "Wholesale trade [this class includes wholesale on a fee basis ...]"
#   "Manufacture of paper (n.e.c.)"
# The gloss opens with a closed set of taxonomy connectives (includes/excludes/
# for example/such as/e.g./i.e./covers/comprises/"this class includes"/n.e.c.).
# That is reference material, never a business's own line-of-business statement.
# Structural/format signature only -- no company names, no activity phrase list.
# A parenthetical that is *not* one of these connectives (a product line, a unit,
# a technology, a location) is left alone.
_CLASSIFICATION_GLOSS_RE = re.compile(
    r"[\[(]\s*"
    r"(?:(?:this|also)\s+)?"
    r"(?:(?:sub[-\s]?class|class|group|division|section|category|heading|code)\s+)?"
    r"(?:"
    r"includes?|excludes?|including|excluding|incl\.?|excl\.?|covers?|comprises?|comprising"
    r"|for\s+example|e\.?\s?g\.?|such\s+as|namely|i\.?\s?e\.?"
    r"|n\.?\s?e\.?\s?c\.?|not\s+elsewhere\s+classified"
    r")\b",
    re.IGNORECASE,
)


def is_valid_business_activity(value: str | None) -> bool:
    """
    Generic structural check that a value looks like a real business-activity /
    line-of-business description rather than navigation text, a search snippet,
    a page dump, a bare field label, or an identifier.

    Purely structural: no company names, no source URLs, no fixed list of "bad"
    activity phrases. It accepts terse-but-real values ("Software", "IT services",
    "Wholesale trade") and rejects menus / snippets / labels / markup.
    """
    if not value or not isinstance(value, str):
        return False
    v = value.strip()
    if len(v) < 3 or len(v) > 250:
        return False

    # A description is a single line, not a multi-field block or a page dump.
    if any(c in v for c in ("\n", "\r", "\t")):
        return False

    low = v.lower()

    # URLs, e-mail addresses, or residual markup are never an activity value.
    if re.search(r"https?://|www\.\w|\S+@\S+\.\S+|<[a-z/][^>]*>|&[a-z]{2,6};", low):
        return False

    # Truncated search snippet / interrogative search query.
    if "…" in v or v.endswith("...") or " ... " in v or "?" in v:
        return False

    # NIC / ISIC classification-dictionary entry ("<class> [Includes ...]"),
    # not a company's own activity description.
    if _CLASSIFICATION_GLOSS_RE.search(v):
        return False

    # Must be predominantly natural language, not an id / code / number blob.
    letters = len(re.findall(r"[A-Za-z]", v))
    if letters / max(len(v), 1) < 0.5:
        return False

    # Identifier shapes (GSTIN / CIN) can never be an activity.
    upper = v.upper()
    if re.search(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b", upper) or \
       re.search(r"\b[UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}\b", upper):
        return False

    words = re.findall(r"[A-Za-z][A-Za-z&/.\-']*", v)
    if not (1 <= len(words) <= 40):
        return False

    # Bare field label with nothing descriptive after it (e.g. "Nature of
    # Business Activities:"). Matched as a grammatical pattern, not a fixed list.
    if re.fullmatch(
        r"(?:the\s+)?(?:nature|type|kind|principal|primary|main|category|class|code|"
        r"description|details?|industry|sector|line)\s+(?:of\s+)?"
        r"(?:business(?:\s+activit(?:y|ies))?|activit(?:y|ies)|industry|operations?|work)"
        r"\s*[:\-–—]?",
        low,
    ):
        return False

    # Navigation-menu shape: several separator-delimited items.
    if len(re.findall(r"\s[|/•·»›▸≫>]\s|\s[–-]\s\S", v)) >= 3:
        return False

    # Navigation menu without separators: many items, each capitalised, and not a
    # single closed-class connective anywhere -> a menu bar, not a description.
    if len(words) >= 5 and all(w[:1].isupper() for w in words):
        if not any(w.lower() in _ACTIVITY_FUNCTION_WORDS for w in words):
            return False

    return True


def normalize_location(location: str | None) -> str | None:
    if not location or not isinstance(location, str):
        return None
    loc = location.strip()
    import html as html_lib
    loc = html_lib.unescape(loc)
    loc = re.sub(r"<[^>]+>", " ", loc)
    loc = re.sub(r"^(?:in\s+|located\s+in\s+|at\s+|from\s+|near\s+|city\s+of\s+|state\s+of\s+)", "", loc, flags=re.IGNORECASE).strip()
    loc = re.sub(r"(?i)\s*[-|–—:]?\s*(?:official\s+website|website|company\s+registration|mca\s+details|search\s+results|cin|gstin)\b.*$", "", loc).strip()
    loc = re.split(r"[?&#]", loc)[0].strip()
    loc = re.sub(r"[.\-–—:|/,\s]+$", "", loc).strip()
    parts = [p.strip().title() for p in re.split(r"[,/|;]+", loc) if p.strip()]
    if not parts:
        return None
    seen = set()
    deduped = []
    for p in parts:
        clean_p = re.sub(r"[.\-–—:|/,\s]+$", "", p).strip()
        if clean_p.lower() not in seen and len(clean_p) >= 2:
            seen.add(clean_p.lower())
            deduped.append(clean_p)
    if not deduped:
        return None
    return ", ".join(deduped)


def extract_address_from_text(
    text: str | None,
    target: str | None = None,
    target_confirmed: bool = False,
) -> str:
    """Extract a registered / establishment address from page text.

    When ``target`` is supplied and the page identity is not confirmed
    (``target_confirmed`` is False), an address is only returned if it is
    explicitly associated with the target on the page (a target name token
    appears in/near the address block). This prevents a source organisation's
    own contact address from being attributed to the investigated entity.
    """
    if not text:
        return "NOT_FOUND"
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    require_assoc = bool(target) and not target_confirmed
    target_tokens = _target_name_tokens(target) if require_assoc else set()
    # If the target has no distinctive legal-name tokens (e.g. a bare
    # GSTIN/CIN), name-association is impossible; fall back to unconstrained
    # extraction. Identifier-conflict pages are already rejected upstream.
    if require_assoc and not target_tokens:
        require_assoc = False
    address_prefixes = [
        "registered address of", "registered office address of",
        "principal place of business", "principal place",
        "registered office address", "registered office", "registered address",
        "establishment address", "corporate office", "office address", "contact address", "address"
    ]
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(neg in line_lower for neg in ["no address", "address not", "not published", "not available", "unknown address", "same address", "similar address"]):
            continue
        if require_assoc and not _address_is_target_associated(lines, i, target_tokens):
            continue
        for prefix in address_prefixes:
            if prefix in line_lower:
                match = re.search(r"\b" + re.escape(prefix) + r"\s*(?:is|:|-)?\s*(.*)", line, re.IGNORECASE)
                if match and len(match.group(1).strip()) > 5:
                    content = match.group(1).strip()
                    content = re.sub(r"^(?:[A-Za-z0-9\s.,&()/-]+\s+is\s+|is\s+|at\s+|is\s+at\s+)", "", content, flags=re.IGNORECASE).strip()
                    content = re.split(r"(?i)\s+(?:business\s*activity|nature\s+of\s+business|activity|gst\s*status|gstin|cin|status|incorporation|registration\s*date|contact|phone|email|website|date\s*of\s*incorporation|pan)\b", content)[0].strip()
                    if "." in content and len(content.split(".")[0]) > 8:
                        content = content.split(".")[0].strip()
                    if is_address_like(content):
                        return content
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if is_address_like(next_line):
                        return next_line
                    # Try combining next 2-3 lines for multi-line addresses
                    cand_block = ", ".join([lines[j] for j in range(i + 1, min(len(lines), i + 4)) if not any(kw in lines[j].lower() for kw in ["nature of business", "business activity", "director", "cin", "gstin"])])
                    if is_address_like(cand_block):
                        return cand_block

    indian_states = {"maharashtra", "karnataka", "delhi", "tamil nadu", "telangana", "gujarat", "west bengal", "haryana", "uttar pradesh", "mumbai", "bengaluru", "bangalore", "chennai", "hyderabad", "kolkata", "pune", "gurgaon", "noida"}
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(neg in line_lower for neg in ["same address", "similar address", "search", "menu", "nature of business"]):
            continue
        if require_assoc and not _address_is_target_associated(lines, i, target_tokens):
            continue
        if re.search(r"\b\d{6}\b", line) and any(state in line_lower for state in indian_states):
            clean_line = re.sub(r"^(?:at\s+|is\s+at\s+|registered\s+address\s+of\s+[A-Za-z0-9\s.,&()/-]+\s+is|principal\s+place\s+of\s+business|registered\s+address|address|office)\s*[:\-]?", "", line, flags=re.IGNORECASE).strip()
            if is_address_like(clean_line):
                return clean_line
            addr_block = [lines[j] for j in range(max(0, i - 2), i + 1) if not any(kw in lines[j].lower() for kw in ["nature of business", "business activity", "search"])]
            cand = " | ".join(addr_block)
            if is_address_like(cand):
                return cand

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
            if (
                cand_clean.lower() not in invalid_activities
                and len(cand_clean) >= 4
                and not re.match(r"^\d+$", cand_clean)
                # NIC/ISIC classification-dictionary text ("<class> [Includes ...]")
                # is reference material, not the target's own activity.
                and not _CLASSIFICATION_GLOSS_RE.search(cand_clean)
            ):
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

    # Directory / registry pages frequently append registration, identifier, age
    # or date metadata to the entity name in the page <title>, e.g.
    #   "<NAME> HAVING CIN <cin> IS 45 YEARS, 2 MONTHS & 2 DAYS OLD"
    #   "<NAME> CIN: <cin> | Company Details"
    #   "<NAME> incorporated on 12-03-1981"
    # A real legal entity name never contains these. Truncate the candidate at
    # the first such marker. Purely structural / grammatical — no source or
    # company names.
    name = re.split(
        r"(?i)\b(?:"
        r"having\s+(?:cin|gstin|llpin|pan|din|tan)\b"
        r"|(?:cin|gstin|llpin|din|pan|tan)\s*[:#=-]"
        r"|is\s+\d+\s+(?:year|yr|month|mo|week|wk|day)s?\b"
        r"|incorporated\s+(?:on|in|as|since)\b"
        r"|registered\s+(?:on|in|at|as|since|under|with|address|office)\b"
        r"|(?:date|year)\s+of\s+(?:incorporation|registration|establishment)\b"
        r"|(?:established|founded|incorporated)\s+(?:on|in)\s+\d"
        r")",
        name,
        maxsplit=1,
    )[0].strip()

    # A statutory identifier token embedded anywhere in the name is metadata,
    # never part of the name itself — cut at the first one.
    name = re.split(
        r"(?i)\b(?:"
        r"[UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}"      # CIN
        r"|[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]"   # GSTIN
        r"|[A-Z]{3}-[0-9]{4}"                                 # LLPIN
        r")\b",
        name,
        maxsplit=1,
    )[0].strip()

    name = re.sub(r"[.,\-–—:|/&\s]+$", "", name).strip()

    # Strip any trailing "in <location>" query contamination generically (e.g. "TATA CONSULTANCY SERVICES LIMITED in maharashtra,india")
    name = re.sub(r"(?i)\s+\bin\s+.*$", "", name).strip()
    name = re.sub(r"(?i)\s+(?:mca\s+company\s+registration|epfo\s+establishment|official\s+website|company\s+registration|master\s+data)\b.*$", "", name).strip()
    name = re.sub(r"(?i),\s*(?:maharashtra|karnataka|delhi|tamil nadu|gujarat|telangana|haryana|uttar pradesh|west bengal|mumbai|bengaluru|bangalore|delhi|new delhi|chennai|hyderabad|pune|kolkata|india|bharat)\b.*$", "", name).strip()

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
        "terms of use", "privacy policy", "all rights reserved", "something went wrong", "error",
        "cin", "gstin", "llpin", "din", "pan", "tan", "having cin", "having gstin",
        "date of incorporation", "date of registration", "company details", "company profile"
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
        or not is_valid_legal_name(name)
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


_GSTIN_RE = re.compile(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z])\b")
_CIN_RE = re.compile(r"\b([UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6})\b")
_NAME_STOP = {
    "pvt", "private", "ltd", "limited", "llp", "llc", "inc", "incorporated",
    "corp", "corporation", "co", "company", "the", "and", "of", "group",
    "india", "official", "website",
}
_LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:pvt\.?|private|ltd\.?|limited|llp|llc|inc\.?|incorporated|corp\.?|corporation|gmbh|s\.?a\.?|plc)\b",
    re.IGNORECASE,
)


def _normalize_identifier(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()


def _distinctive_name_tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(t) > 2 and t not in _NAME_STOP
    }


# Planner appends these hint words to a target string (e.g. "<name> EPFO
# establishment", "<name> MCA company registration"). They are not part of the
# legal name and must not count as distinctive identity tokens.
_SEARCH_HINT_WORDS = {
    "epfo", "epf", "mca", "gst", "gstin", "cin", "establishment", "registration",
    "registered", "company", "companies", "master", "data", "portal", "search",
    "records", "record", "verification", "verify", "profile", "details", "detail",
    "financials", "corporate", "affairs", "ministry", "taxpayer",
}


def _strip_search_hints(name: str | None) -> str:
    """Drop a trailing run of planner-appended search-hint words from a target."""
    if not name:
        return ""
    toks = str(name).split()
    while toks and re.sub(r"[^a-z]", "", toks[-1].lower()) in _SEARCH_HINT_WORDS:
        toks.pop()
    return " ".join(toks)


def _target_name_tokens(target: str) -> set[str]:
    """Distinctive tokens of the target's legal name, excluding identifier tokens
    and planner search hints."""
    stripped = _strip_search_hints(target)
    stripped = _GSTIN_RE.sub(" ", stripped.upper())
    stripped = _CIN_RE.sub(" ", stripped)
    return {t for t in _distinctive_name_tokens(stripped) if t not in _SEARCH_HINT_WORDS}


_LEGAL_NAME_SPAN_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9&.\-'()/ ]{2,110}?\b"
    r"(?:pvt\.?|private|ltd\.?|limited|llp|llc|inc\.?|incorporated|corp\.?|corporation|gmbh|plc))\b",
    re.IGNORECASE,
)


def _page_claimed_name_tokens(page_title: str, page_text: str) -> set[str]:
    """
    Distinctive tokens of the *legal name the page itself claims to be about* -
    from the page title (before any " - "/"|" boilerplate) and the first
    legal-name span in the leading page text. Only the entity-name span is used,
    not the surrounding line, so status/label words don't leak in.
    """
    tokens: set[str] = set()

    title_head = re.split(r"\s+[-|–—:]\s+|\s*\|\s*", (page_title or ""))[0]
    m = _LEGAL_NAME_SPAN_RE.search(title_head)
    tokens |= {t for t in _distinctive_name_tokens(m.group(1) if m else title_head)
               if t not in _SEARCH_HINT_WORDS}

    for line in (page_text or "")[:2500].split("\n"):
        m = _LEGAL_NAME_SPAN_RE.search(line.strip())
        if m:
            cand = {t for t in _distinctive_name_tokens(m.group(1)) if t not in _SEARCH_HINT_WORDS}
            if cand:
                tokens |= cand
                break
    return tokens


def third_party_identity_verdict(target: str, page_title: str, page_text: str) -> str:
    """
    Whether a third-party directory / registry page describes ``target``.

    Returns one of:
      * ``"MATCH"``       - positive multi-attribute agreement: a shared strong
                            identifier (GSTIN/CIN), or a near-complete legal-name
                            match with no competing entity name.
      * ``"CONFLICT"``    - a strong identifier disagrees, or the page names a
                            clearly different incorporated entity.
      * ``"INSUFFICIENT"``- identity cannot be positively confirmed (e.g. only a
                            single shared generic/name token).

    Generic: no company names, identifiers, or addresses are special-cased.
    """
    title = page_title or ""
    text = page_text or ""
    combined_up = f"{title}\n{text}".upper()
    target_up = (target or "").upper()

    # 1. Strong identifiers.
    id_confirmed = False
    for rx in (_GSTIN_RE, _CIN_RE):
        t_ids = {_normalize_identifier(m) for m in rx.findall(target_up)}
        if not t_ids:
            continue
        p_ids = {_normalize_identifier(m) for m in rx.findall(combined_up)}
        if not p_ids:
            continue
        if t_ids & p_ids:
            id_confirmed = True
        else:
            return "CONFLICT"
    if id_confirmed:
        return "MATCH"

    # 2. Legal-name agreement.
    t_tokens = _target_name_tokens(target)
    if not t_tokens:
        return "INSUFFICIENT"

    p_tokens = _page_claimed_name_tokens(title, text)
    if not p_tokens:
        return "INSUFFICIENT"

    shared = t_tokens & p_tokens
    recall = len(shared) / len(t_tokens)
    page_extra = p_tokens - t_tokens  # distinctive tokens the page's entity has

    # A single shared token (when the target has several) is never enough.
    if len(shared) <= 1 and len(t_tokens) >= 2:
        return "CONFLICT" if page_extra else "INSUFFICIENT"

    if recall >= 1.0 and not page_extra:
        return "MATCH"
    if recall >= 0.6 and not page_extra:
        # page name is a subset/prefix of the target name, nothing contradicts it
        return "MATCH"
    if page_extra and recall < 1.0:
        # page names a different incorporated entity that shares only part of
        # the target's name
        return "CONFLICT"
    return "INSUFFICIENT"


def _address_is_target_associated(lines: list[str], idx: int, target_tokens: set[str]) -> bool:
    """A candidate address at ``lines[idx]`` is target-associated when a target
    legal-name token appears on that line or within the few lines above it (the
    label / heading that introduces the address)."""
    if not target_tokens:
        return False
    window = " ".join(lines[max(0, idx - 3): idx + 1]).lower()
    return any(tok in window for tok in target_tokens)


def page_conflicts_with_target(target: str, page_title: str, page_text: str) -> bool:
    """
    Generic, structural check that a fetched page *positively identifies a
    different legal entity* than ``target`` -- used to discard wrong-company
    registry pages before extraction.

    It returns True only when there is affirmative evidence of a mismatch:
      * the target carries a GSTIN / CIN and the page shows one of the same kind
        that does not match (normalised, hyphen/space tolerant), or
      * the target has >=2 distinctive name tokens, the page title names a
        legal entity (has an incorporation suffix), and shares none of them.

    "Cannot confirm identity" is NOT a conflict -- those pages are left for the
    downstream semantic / identifier validation to judge.
    """
    target = target or ""
    title = page_title or ""
    text = page_text or ""
    combined = f"{title}\n{text}"

    t_up = target.upper()
    for rx in (_GSTIN_RE, _CIN_RE):
        t_ids = {_normalize_identifier(m) for m in rx.findall(t_up)}
        if not t_ids:
            continue
        p_ids = {_normalize_identifier(m) for m in rx.findall(combined.upper())}
        if p_ids and not (t_ids & p_ids):
            return True

    t_tokens = _distinctive_name_tokens(target)
    if len(t_tokens) >= 2:
        title_tokens = _distinctive_name_tokens(title)
        if title_tokens and _LEGAL_SUFFIX_RE.search(title) and not (t_tokens & title_tokens):
            # Title names an incorporated entity, none of the target's
            # distinctive tokens are anywhere on the page -> different company.
            if not (t_tokens & _distinctive_name_tokens(text[:2000])):
                return True

    return False


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
