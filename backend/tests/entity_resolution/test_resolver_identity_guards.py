"""
Regression for the two entity-resolution correctness gaps:

  GAP A - a matching identifier (incl. website) must never override a
          *conflicting* statutory identifier (GSTIN/CIN) on the same candidate.
  GAP B - a similarity match must not rest on a single non-distinctive name
          token with no corroborating identity attribute.

No company-specific values are special-cased.
"""
from app.entity_resolution.resolver import resolve_entity


# --------------------------------------------------------------------------- #
# GAP A - conflicting statutory identifiers
# --------------------------------------------------------------------------- #
def test_1_matching_website_plus_conflicting_cin_is_not_a_match():
    r = resolve_entity(
        {"name": "ACME WIDGETS PRIVATE LIMITED", "cin": "U11111DL2001PLC000001", "website": "acme.example"},
        [{"name": "ACME WIDGETS PRIVATE LIMITED", "cin": "U99999MH2010PLC999999", "website": "acme.example"}],
    )
    assert r["matched"] is False
    assert r["match_type"] == "CONFLICTING_IDENTITY"
    assert r["resolution_status"] == "CONFLICTING_IDENTITY"
    assert r["confidence"] == 0.0
    assert r["entity"] is None
    assert any(c["field"] == "cin" for c in r["conflicting_identifiers"])


def test_2_matching_gstin_plus_conflicting_cin_is_not_a_match():
    r = resolve_entity(
        {"name": "ACME WIDGETS PRIVATE LIMITED", "gstin": "27AAAAA0000A1Z5", "cin": "U11111DL2001PLC000001"},
        [{"name": "ACME WIDGETS PRIVATE LIMITED", "gstin": "27AAAAA0000A1Z5", "cin": "U99999MH2010PLC999999"}],
    )
    assert r["matched"] is False
    assert r["match_type"] == "CONFLICTING_IDENTITY"
    assert r["confidence"] == 0.0


def test_3_matching_website_plus_conflicting_gstin_is_not_a_match():
    r = resolve_entity(
        {"name": "ACME", "gstin": "27AAAAA0000A1Z5", "website": "shared.example"},
        [{"name": "OTHER HOLDINGS", "gstin": "09BBBBB1111B1Z9", "website": "shared.example"}],
    )
    assert r["matched"] is False
    assert r["match_type"] == "CONFLICTING_IDENTITY"
    assert r["confidence"] == 0.0


def test_gap_a_non_conflicting_exact_match_still_wins_when_another_candidate_conflicts():
    # candidate 0 conflicts on CIN; candidate 1 matches CIN cleanly -> MATCH on candidate 1
    target = {"name": "ACME WIDGETS PRIVATE LIMITED", "cin": "U11111DL2001PLC000001"}
    cands = [
        {"name": "ACME WIDGETS PRIVATE LIMITED", "cin": "U99999MH2010PLC999999", "website": "acme.example"},
        {"name": "ACME WIDGETS PRIVATE LIMITED", "cin": "U11111DL2001PLC000001"},
    ]
    # give candidate 0 a matching website so it would be an "exact match" too
    target["website"] = "acme.example"
    r = resolve_entity(target, cands)
    assert r["matched"] is True
    assert r["match_type"] == "EXACT"
    assert r["entity"] == cands[1]


# --------------------------------------------------------------------------- #
# GAP B - insufficient identity
# --------------------------------------------------------------------------- #
def test_4_generic_single_token_name_only_is_insufficient_identity():
    r = resolve_entity({"name": "Solutions"}, [{"name": "Solutions"}])
    assert r["matched"] is False
    assert r["match_type"] == "INSUFFICIENT_IDENTITY"
    assert r["resolution_status"] == "ENTITY_UNRESOLVED"
    # confidence carries the raw name similarity (mirrors the NO_MATCH branch);
    # the not-a-match signal is match_type / matched, not a zeroed score.
    assert r["confidence"] >= 0.75
    assert r["entity"] is None


def test_5_short_generic_name_only_is_insufficient_identity():
    for nm in ("AB", "Global", "Enterprises"):
        r = resolve_entity({"name": nm}, [{"name": nm}])
        assert r["matched"] is False, nm
        assert r["match_type"] == "INSUFFICIENT_IDENTITY", nm


# --------------------------------------------------------------------------- #
# preservation - legitimate matches are untouched
# --------------------------------------------------------------------------- #
def test_6_legitimate_multi_token_name_only_match_still_works():
    r = resolve_entity({"name": "ABC Foods Pvt Ltd"}, [{"name": "abc foods private limited"}])
    assert r["matched"] is True
    assert r["match_type"] == "SIMILARITY"
    assert r["confidence"] >= 0.75


def test_7_legitimate_similarity_with_corroborating_attributes_still_works():
    r = resolve_entity(
        {"name": "ABC Foods Pvt Ltd", "location": "Noida", "website": "abcfoods.in"},
        [{"name": "abc foods pvt ltd", "location": "NOIDA", "website": "https://www.abcfoods.in/about"}],
    )
    assert r["matched"] is True
    assert r["match_type"] in {"EXACT", "SIMILARITY"}
    assert r["confidence"] >= 0.75


def test_gap_b_single_token_name_with_corroborating_website_still_matches():
    r = resolve_entity(
        {"name": "Solutions", "website": "solutions.example"},
        [{"name": "Solutions", "website": "solutions.example"}],
    )
    assert r["matched"] is True
    assert r["match_type"] in {"EXACT", "SIMILARITY"}


def test_clean_exact_identifier_match_unaffected():
    r = resolve_entity(
        {"name": "ACME WIDGETS PRIVATE LIMITED", "gstin": "27AAAAA0000A1Z5"},
        [{"name": "ACME WIDGETS PRIVATE LIMITED", "gstin": "27AAAAA0000A1Z5"}],
    )
    assert r["matched"] is True and r["match_type"] == "EXACT" and r["confidence"] == 1.0
